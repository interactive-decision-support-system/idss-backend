import pandas as pd
import openai
import json
import dotenv
import os
dotenv.load_dotenv()

# Configuration
API_KEY = os.getenv("OPENAI_API_KEY")
INPUT_FILE = "query_data.csv"
OUTPUT_FILE = "query_data_enriched.csv"
client = openai.OpenAI(api_key=API_KEY)

# #BLANK: INSERT YOUR EXTRACTION CONSTRAINTS HERE
# (Example: "Extract price as an integer, GPU model, and RAM in GB")
FILTER_INSTRUCTIONS = """
#BLANK 
"""

def enrich_queries(row):
    # Only process if the LLM fields are empty
    if pd.isna(row['shortened_query']) or row['shortened_query'] == "":
        print(f"Processing ID: {row['id']}")
        
        prompt = f"""
        You are a data labeling assistant for a laptop recommendation system.
        
        Original User Request: "{row['original_query']}"
        
        Task:
        1. Create a 'shortened_query': Summarize the request into a maximum of 3 clear sentences.
        2. Create a 'compact_query': Reduce the request to 3-5 high-impact keywords.
        For the shortened and compact versions, focus on retaining the core intent of the user looking for a laptop, and remove everything that is not essential to understanding their needs like questions from a questionnaire.

        3. 'extracted_criteria': Based on the instructions below, extract the filters.
        
        Extraction Instructions:
        {FILTER_INSTRUCTIONS}
        
        Return ONLY a JSON object with keys: "shortened", "compact", "criteria".
        """

        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                response_format={ "type": "json_object" }
            )
            
            res_data = json.loads(response.choices[0].message.content)
            row['shortened_query'] = res_data['shortened']
            row['compact_query'] = res_data['compact']
            row['extracted_criteria'] = json.dumps(res_data['criteria']) # Store as JSON string
        except Exception as e:
            print(f"Error on ID {row['id']}: {e}")
            
    return row

# Execution
df = pd.read_csv(INPUT_FILE)
df = df.apply(enrich_queries, axis=1)
df.to_csv(OUTPUT_FILE, index=False)
print(f"Enrichment complete. Saved to {OUTPUT_FILE}")