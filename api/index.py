"""
Vercel Serverless API for IDSS.

This is a lightweight version that works within Vercel's constraints by using:
- OpenAI Embeddings API (instead of local sentence-transformers)
- External database (Supabase/Neon) instead of local SQLite
- Pinecone/Qdrant for vector search instead of local FAISS

Environment variables required:
- OPENAI_API_KEY: For embeddings and LLM
- DATABASE_URL: PostgreSQL connection string (Supabase/Neon)
- PINECONE_API_KEY: For vector search (optional)
- PINECONE_INDEX: Pinecone index name (optional)
"""
import os
import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from typing import Dict, Any, List, Optional

# Use OpenAI for embeddings (lightweight, no local models)
from openai import OpenAI


# Simple in-memory session storage (note: won't persist across function invocations)
# For production, use Redis or database-backed sessions
sessions: Dict[str, dict] = {}


def get_openai_client():
    """Get OpenAI client."""
    return OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


def get_embedding(text: str) -> List[float]:
    """Get embedding using OpenAI API."""
    client = get_openai_client()
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return response.data[0].embedding


def parse_user_input_simple(message: str, client: OpenAI) -> dict:
    """Simple LLM-based parsing without heavy dependencies."""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": """Extract vehicle search criteria from the user message.
Return JSON with:
- filters: {make, model, body_style, price_min, price_max, year_min, year_max, fuel_type, drivetrain}
- preferences: {liked_features: [], disliked_features: [], notes: ""}
- is_impatient: boolean (true if user wants results immediately)

Only include fields that are explicitly mentioned. Use null for unspecified fields."""
            },
            {"role": "user", "content": message}
        ],
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)


def generate_question_simple(session: dict, client: OpenAI) -> dict:
    """Generate a clarifying question."""
    history = session.get("conversation_history", [])
    filters = session.get("filters", {})

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": """You are a helpful car shopping assistant. Based on the conversation,
ask ONE clarifying question to better understand the user's needs.

Return JSON with:
- question: "Your question here?"
- topic: "budget|body_style|features|brand|usage" (what aspect you're asking about)
- quick_replies: ["Option 1", "Option 2", "Option 3"] (3-4 common answers)

Focus on: budget, body style, must-have features, preferred brands, or primary use case."""
            },
            *[{"role": m["role"], "content": m["content"]} for m in history[-6:]],
            {"role": "user", "content": f"Current filters: {json.dumps(filters)}"}
        ],
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)


def generate_recommendations_intro(filters: dict, client: OpenAI) -> str:
    """Generate introduction message for recommendations."""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "Write a brief, friendly 1-2 sentence intro for vehicle recommendations based on the user's criteria. Be concise."
            },
            {"role": "user", "content": f"User criteria: {json.dumps(filters)}"}
        ]
    )
    return response.choices[0].message.content


class handler(BaseHTTPRequestHandler):
    """Vercel serverless function handler."""

    def do_GET(self):
        """Handle GET requests."""
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "":
            self.send_json_response({
                "status": "online",
                "service": "IDSS API (Vercel)",
                "version": "1.0.0",
                "note": "Lightweight version using OpenAI embeddings"
            })
        elif path == "/status":
            self.send_json_response({
                "status": "online",
                "runtime": "vercel-serverless",
                "sessions": len(sessions)
            })
        elif path.startswith("/session/"):
            session_id = path.split("/session/")[1]
            if session_id in sessions:
                self.send_json_response(sessions[session_id])
            else:
                self.send_error_response(404, "Session not found")
        else:
            self.send_error_response(404, "Not found")

    def do_POST(self):
        """Handle POST requests."""
        parsed = urlparse(self.path)
        path = parsed.path

        # Read request body
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')

        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self.send_error_response(400, "Invalid JSON")
            return

        if path == "/chat":
            self.handle_chat(data)
        elif path == "/session/reset":
            self.handle_reset(data)
        else:
            self.send_error_response(404, "Not found")

    def handle_chat(self, data: dict):
        """Handle chat request."""
        try:
            message = data.get("message", "")
            session_id = data.get("session_id")

            # Get or create session
            if not session_id or session_id not in sessions:
                import uuid
                session_id = session_id or str(uuid.uuid4())
                sessions[session_id] = {
                    "session_id": session_id,
                    "filters": {},
                    "preferences": {},
                    "conversation_history": [],
                    "question_count": 0,
                    "k": 3  # Default questions to ask
                }

            session = sessions[session_id]
            client = get_openai_client()

            # Parse user input
            parsed = parse_user_input_simple(message, client)

            # Update session
            if parsed.get("filters"):
                for k, v in parsed["filters"].items():
                    if v is not None:
                        session["filters"][k] = v

            if parsed.get("preferences"):
                for k, v in parsed["preferences"].items():
                    if v:
                        session["preferences"][k] = v

            session["conversation_history"].append({
                "role": "user",
                "content": message
            })

            # Decide: ask question or recommend
            should_recommend = (
                session["question_count"] >= session["k"] or
                parsed.get("is_impatient", False)
            )

            if should_recommend:
                # Generate recommendations intro
                intro = generate_recommendations_intro(session["filters"], client)

                session["conversation_history"].append({
                    "role": "assistant",
                    "content": intro
                })

                self.send_json_response({
                    "response_type": "recommendations",
                    "message": intro,
                    "session_id": session_id,
                    "filters": session["filters"],
                    "preferences": session["preferences"],
                    "question_count": session["question_count"],
                    "recommendations": [],  # Would come from external vector DB
                    "note": "Connect Pinecone/Supabase for actual vehicle data"
                })
            else:
                # Generate question
                question_data = generate_question_simple(session, client)
                session["question_count"] += 1

                session["conversation_history"].append({
                    "role": "assistant",
                    "content": question_data["question"]
                })

                self.send_json_response({
                    "response_type": "question",
                    "message": question_data["question"],
                    "session_id": session_id,
                    "quick_replies": question_data.get("quick_replies", []),
                    "filters": session["filters"],
                    "preferences": session["preferences"],
                    "question_count": session["question_count"]
                })

        except Exception as e:
            self.send_error_response(500, str(e))

    def handle_reset(self, data: dict):
        """Handle session reset."""
        import uuid
        session_id = data.get("session_id") or str(uuid.uuid4())

        sessions[session_id] = {
            "session_id": session_id,
            "filters": {},
            "preferences": {},
            "conversation_history": [],
            "question_count": 0,
            "k": 3
        }

        self.send_json_response({
            "session_id": session_id,
            "status": "reset"
        })

    def send_json_response(self, data: dict, status: int = 200):
        """Send JSON response."""
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def send_error_response(self, status: int, message: str):
        """Send error response."""
        self.send_json_response({"error": message}, status)

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
