"""
Local vehicle data access layer backed by SQLite.

Provides filtered queries against the prebuilt uni_vehicles.db dataset
and returns results shaped like the Auto.dev payloads expected by downstream
components.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from idss.utils.logger import get_logger

logger = get_logger("data.vehicle_store")

NORMALIZED_COLUMN_MAP = {
    "body_style": "norm_body_type",
    "fuel_type": "norm_fuel_type",
    "is_used": "norm_is_used",
}


def _normalized_column_expr(column: str) -> str:
    """Return normalized column name if available, otherwise return original column."""
    norm_col = NORMALIZED_COLUMN_MAP.get(column)
    if norm_col:
        return norm_col
    return column


def _project_root() -> Path:
    """Return project root (parent of idss package)."""
    return Path(__file__).resolve().parent.parent.parent


DEFAULT_DB_PATH = _project_root() / "data" / "car_dataset_idss" / "uni_vehicles.db"


class VehicleStoreError(RuntimeError):
    """Raised when the local vehicle store encounters an error."""


def _format_sql_with_params(sql: str, params: Sequence[Any]) -> str:
    """Return human-readable SQL with positional parameters substituted for logging."""
    formatted = sql
    for value in params:
        replacement = repr(value)
        formatted = formatted.replace("?", replacement, 1)
    return formatted


def _parse_numeric_range(value: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Parse numeric range strings like "10000-30000" or "2020".

    Returns:
        Tuple[min_value, max_value] where any element can be None.
    """
    if not value:
        return (None, None)

    value = value.strip()
    if "-" not in value:
        try:
            num = float(value)
            return (num, num)
        except ValueError:
            return (None, None)

    lower, upper = value.split("-", 1)
    lower_val = float(lower) if lower.strip() else None
    upper_val = float(upper) if upper.strip() else None
    return (lower_val, upper_val)


def _split_multi_value(text: str) -> List[str]:
    """Split comma-separated filters into individual trimmed values."""
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def _haversine_distance_sql(user_lat: float, user_lon: float) -> str:
    """
    Generate SQL expression for haversine distance calculation in miles.

    Returns SQL expression that calculates distance from user location to vehicle's dealer location.
    Formula: https://en.wikipedia.org/wiki/Haversine_formula

    Args:
        user_lat: User's latitude
        user_lon: User's longitude

    Returns:
        SQL expression string for distance calculation in miles
    """
    # Earth's radius in miles
    earth_radius = 3959.0

    # Convert degrees to radians in SQL
    # SQLite uses radians for trig functions
    return f"""
        ({earth_radius} * 2 * ASIN(SQRT(
            POW(SIN((RADIANS(dealer_latitude) - RADIANS({user_lat})) / 2), 2) +
            COS(RADIANS({user_lat})) * COS(RADIANS(dealer_latitude)) *
            POW(SIN((RADIANS(dealer_longitude) - RADIANS({user_lon})) / 2), 2)
        )))
    """


@dataclass
class SupabaseVehicleStore:
    """
    Vehicle data access layer backed by Supabase.
    """
    client: Any = None
    require_photos: bool = True

    def __post_init__(self) -> None:
        from idss.utils.supabase_client import supabase
        self.client = supabase

    def search_listings(
        self,
        filters: Dict[str, Any],
        limit: int = 200,
        offset: int = 0,
        order_by: str = "price",
        order_dir: str = "ASC",
        user_latitude: Optional[float] = None,
        user_longitude: Optional[float] = None,
        max_per_make_model: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute filtered search against Supabase 'cars' table.
        """
        url_params = {
            "select": "*",
            "limit": str(limit),
            "offset": str(offset)
        }
        
        # Mapping sorting
        order_col = order_by if order_by in ("price", "mileage", "year") else "price"
        direction = "asc" if order_dir.upper() == "ASC" else "desc"
        url_params["order"] = f"{order_col}.{direction}"

        # Helper to add params safely (handling multiple conditions for the same key)
        def add_param(k: str, v: str):
            if k in url_params:
                if isinstance(url_params[k], list):
                    url_params[k].append(v)
                else:
                    url_params[k] = [url_params[k], v]
            else:
                url_params[k] = v

        # Apply filters
        for key, val in filters.items():
            if not val: continue
            
            if key == "make":
                add_param("make", f"ilike.{val}")
            elif key == "model":
                add_param("model", f"ilike.{val}")
            elif key == "year":
                lower, upper = _parse_numeric_range(str(val))
                if lower is not None: add_param("year", f"gte.{int(lower)}")
                if upper is not None: add_param("year", f"lte.{int(upper)}")
            elif key == "price":
                lower, upper = _parse_numeric_range(str(val))
                # Enforce a minimum price of $1 to filter out $0 test listings
                min_price = max(1, int(lower)) if lower is not None else 1
                add_param("price", f"gte.{min_price}")
                if upper is not None: add_param("price", f"lte.{int(upper)}")
            elif key == "mileage":
                lower, upper = _parse_numeric_range(str(val))
                if lower is not None: add_param("mileage", f"gte.{int(lower)}")
                if upper is not None: add_param("mileage", f"lte.{int(upper)}")
            elif key == "body_style":
                add_param("norm_body_type", f"eq.{val}")
            elif key == "fuel_type":
                add_param("norm_fuel_type", f"eq.{val}")
            elif key == "is_used":
                add_param("norm_is_used", f"eq.{1 if val else 0}")
        
        if self.require_photos:
            # PostGREST: Use 'not.is.null' as the filter value
            url_params["primary_image_url"] = "not.is.null"

        # Call Supabase with stratified price sampling
        # When a price range is present, we split the range into bands and fetch
        # limit/n_strata vehicles from each band so the candidate pool spans the
        # full price range rather than always picking the cheapest cars.
        try:
            effective_limit = min(limit, 100)
            price_filter = filters.get("price") if filters else None
            
            payloads: List[Dict[str, Any]] = []

            if price_filter:
                lower, upper = _parse_numeric_range(str(price_filter))
                min_p = int(max(1, lower)) if lower is not None else 1
                max_p = int(upper) if upper is not None else 999_999
                n_strata = 4
                per_stratum = max(effective_limit // n_strata, 5)
                step = (max_p - min_p) / n_strata
                seen_vins: set = set()
                for i in range(n_strata):
                    band_lo = int(min_p + i * step)
                    band_hi = int(min_p + (i + 1) * step) if i < n_strata - 1 else max_p
                    # Build per-band params
                    band_params = {k: v for k, v in url_params.items() if k != "price" and k != "order"}
                    band_params["price"] = [f"gte.{band_lo}", f"lte.{band_hi}"]
                    band_params["order"] = "price.asc"
                    band_params["limit"] = str(per_stratum)
                    resp = self.client.client.get("/rest/v1/cars", params=band_params)
                    if resp.status_code == 200:
                        for row in resp.json():
                            vin = row.get("vin")
                            if vin and vin not in seen_vins:
                                seen_vins.add(vin)
                                payloads.append(self._row_to_payload(row))
                
                # Shuffle so nearby price bands don't cluster in ranking
                import random
                random.shuffle(payloads)
                
                # Trim to effective_limit
                payloads = payloads[:effective_limit]
                
            else:
                url_params["limit"] = str(effective_limit)
                response = self.client.client.get("/rest/v1/cars", params=url_params)
                if response.status_code == 500:
                    logger.warning("Supabase 500 error, retrying without photo filter")
                    url_params.pop("primary_image_url", None)
                    response = self.client.client.get("/rest/v1/cars", params=url_params)
                response.raise_for_status()
                for row in response.json():
                    payloads.append(self._row_to_payload(row))

            return payloads
        except Exception as e:
            logger.error(f"Supabase search failed: {e}")
            return []


    def get_by_vin(self, vin: str) -> Optional[Dict[str, Any]]:
        res = self.client.select("cars", filters={"vin": vin.upper()}, limit=1)
        return self._row_to_payload(res[0]) if res else None

    def _row_to_payload(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Adapt Supabase row (dict) to downstream format."""
        raw_json = row.get("raw_json")
        if raw_json and isinstance(raw_json, str):
            try:
                payload = json.loads(raw_json)
            except:
                payload = row.copy()
        else:
            payload = row.copy()

        # Reconstruct Auto.dev format
        vehicle_data = {
            "vin": row.get("vin"),
            "year": row.get("year"),
            "make": row.get("make"),
            "model": row.get("model"),
            "trim": row.get("trim"),
            "price": row.get("price"),
            "mileage": row.get("mileage"),
            "bodyStyle": row.get("body_style") or row.get("norm_body_type"),
            "drivetrain": row.get("drivetrain"),
            "engine": row.get("engine"),
            "fuel": row.get("fuel_type") or row.get("norm_fuel_type"),
            "transmission": row.get("transmission"),
            "doors": row.get("doors"),
            "seats": row.get("seats"),
            "exteriorColor": row.get("exterior_color"),
            "interiorColor": row.get("interior_color"),
            "build_city_mpg": row.get("build_city_mpg"),
            "build_highway_mpg": row.get("build_highway_mpg"),
            "norm_body_type": row.get("norm_body_type"),
            "norm_fuel_type": row.get("norm_fuel_type"),
            "norm_is_used": row.get("norm_is_used"),
        }

        retail_data = {
            "price": row.get("price"),
            "miles": row.get("mileage"),
            "dealer": row.get("dealer_name"),
            "city": row.get("dealer_city"),
            "state": row.get("dealer_state"),
            "zip": row.get("dealer_zip"),
            "vdp": row.get("vdp_url"),
            "carfaxUrl": row.get("carfax_url"),
            "primaryImage": row.get("primary_image_url"),
            "photoCount": row.get("photo_count"),
            "used": bool(row.get("is_used", True)),
            "cpo": bool(row.get("is_cpo", False)),
        }

        return {
            "@id": f"supabase/{row.get('vin')}",
            "vin": row.get("vin"),
            "online": True,
            "vehicle": vehicle_data,
            "retailListing": retail_data,
            "_original": payload,
        }

@dataclass
class LocalVehicleStore:
    """
    Thin repository for vehicle listings stored in SQLite.

    Args:
        db_path: Optional override for database location.
        require_photos: Whether to filter listings to those with photo metadata.
    """

    db_path: Optional[Path] = None
    require_photos: bool = True
    last_sql_query: Optional[str] = None  # Stores the last executed SQL query (formatted with params)

    def __post_init__(self) -> None:
        path = Path(self.db_path) if self.db_path else DEFAULT_DB_PATH
        if not path.exists():
            raise FileNotFoundError(
                f"Local vehicle database not found at {path}. "
                "Ensure the data symlink is set up correctly."
            )
        self.db_path = path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _row_to_payload(self, row: sqlite3.Row) -> Optional[Dict[str, Any]]:
        """Adapt a SQLite row to the downstream Auto.dev-like JSON format."""
        if not row:
            return None

        # raw_json contains the full Auto.dev payload
        raw_json_str = row["raw_json"]
        if not raw_json_str:
            return None

        try:
            payload = json.loads(raw_json_str)
        except json.JSONDecodeError:
            return None

        # Re-inject/override some fields from the schema for consistency
        # Some are useful for direct access without parsing JSON every time
        vehicle_data = payload.get("vehicle", {})
        vehicle_data.update({
            "vin": row["vin"],
            "year": row["year"],
            "make": row["make"],
            "model": row["model"],
            "trim": row["trim"],
            "price": row["price"],
            "mileage": row["mileage"],
            "bodyStyle": row["body_style"],
            "norm_body_type": row["norm_body_type"],
            "norm_fuel_type": row["norm_fuel_type"],
            "norm_is_used": row["norm_is_used"],
        })

        # Ensure retailListing fields are sync'd
        retail_data = payload.get("retailListing", {})
        retail_data.update({
            "price": row["price"],
            "miles": row["mileage"],
        })

        return payload

    # ------------------------------------------------------------------ #
    # Public interface
    # ------------------------------------------------------------------ #

    def search_listings(
        self,
        filters: Dict[str, Any],
        limit: int = 200,
        offset: int = 0,
        order_by: str = "price",
        order_dir: str = "ASC",
        user_latitude: Optional[float] = None,
        user_longitude: Optional[float] = None,
        max_per_make_model: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute a filtered search against the local database.

        Args:
            filters: Explicit filter dictionary (VehicleFilters).
            limit: Maximum number of rows to return.
            offset: Offset for pagination.
            order_by: Column to sort by (price, mileage, year).
            order_dir: Sort direction ("ASC" or "DESC").
            user_latitude: Optional user latitude for distance filtering.
            user_longitude: Optional user longitude for distance filtering.
            max_per_make_model: Optional limit on vehicles per make/model combination
                               (enforces diversity via SQL window functions)

        Returns:
            List of listing payloads shaped like Auto.dev responses.
        """
        sql, params = self._build_query(
            filters,
            limit,
            offset,
            order_by,
            order_dir,
            user_latitude,
            user_longitude,
            max_per_make_model,
        )
        sql_single_line = " ".join(sql.split())
        formatted_sql = _format_sql_with_params(sql_single_line, params)

        # Store the formatted SQL query for later retrieval
        self.last_sql_query = formatted_sql

        logger.info(
            "Recommendation SQL query: %s",
            formatted_sql,
        )
        logger.debug("Executing local vehicle query: %s | params=%s", sql, params)

        try:
            with self._connect() as conn:
                rows = conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise VehicleStoreError(f"SQLite query failed: {exc}") from exc

        payloads: List[Dict[str, Any]] = []
        for row in rows:
            payload = self._row_to_payload(row)
            if payload:
                payloads.append(payload)

        logger.info("Local vehicle query returned %d listings", len(payloads))
        return payloads

    def get_by_vin(self, vin: str) -> Optional[Dict[str, Any]]:
        """Fetch a single listing by VIN."""
        if not vin:
            return None

        sql = """SELECT raw_json, price, mileage, primary_image_url, photo_count,
            year, make, model, trim, body_style, drivetrain, engine, fuel_type, transmission,
            doors, seats, exterior_color, interior_color,
            dealer_name, dealer_city, dealer_state, dealer_zip, dealer_latitude, dealer_longitude,
            is_used, is_cpo, vdp_url, carfax_url, vin, build_city_mpg, build_highway_mpg,
            norm_body_type, norm_fuel_type, norm_is_used
            FROM unified_vehicle_listings WHERE vin = ? LIMIT 1"""

        try:
            with self._connect() as conn:
                row = conn.execute(sql, (vin.upper(),)).fetchone()
        except sqlite3.Error as exc:
            raise VehicleStoreError(f"Failed to load VIN {vin}: {exc}") from exc

        return self._row_to_payload(row) if row else None

    # ------------------------------------------------------------------ #
    # Query construction helpers
    # ------------------------------------------------------------------ #

    def _build_query(
        self,
        filters: Dict[str, Any],
        limit: int,
        offset: int,
        order_by: str,
        order_dir: str,
        user_latitude: Optional[float] = None,
        user_longitude: Optional[float] = None,
        max_per_make_model: Optional[int] = None,
    ) -> Tuple[str, Tuple[Any, ...]]:
        """Construct SQL query and parameter tuple from explicit filters."""
        select_clause = """SELECT raw_json, price, mileage, primary_image_url, photo_count,
            year, make, model, trim, body_style, drivetrain, engine, fuel_type, transmission,
            doors, seats, exterior_color, interior_color,
            dealer_name, dealer_city, dealer_state, dealer_zip, dealer_latitude, dealer_longitude,
            is_used, is_cpo, vdp_url, carfax_url, vin, build_city_mpg, build_highway_mpg,
            norm_body_type, norm_fuel_type, norm_is_used
            FROM unified_vehicle_listings"""
        conditions: List[str] = []
        params: List[Any] = []

        def add_condition(condition: str, values: Iterable[Any]) -> None:
            conditions.append(condition)
            params.extend(values)

        # Make / model / trim support multiple values
        for column, key in [
            ("make", "make"),
            ("model", "model"),
            ("trim", "trim"),
            ("body_style", "body_style"),
            ("engine", "engine"),
            ("transmission", "transmission"),
            ("drivetrain", "drivetrain"),
            ("fuel_type", "fuel_type"),
            ("exterior_color", "exterior_color"),
            ("interior_color", "interior_color"),
        ]:
            value = filters.get(key)
            values = _split_multi_value(value) if isinstance(value, str) else []
            if values:
                placeholders = ",".join(["?"] * len(values))
                column_expr = _normalized_column_expr(column)
                add_condition(
                    f"UPPER({column_expr}) IN ({placeholders})",
                    [v.upper() for v in values],
                )

        # Door count
        if filters.get("doors"):
            add_condition("doors = ?", (filters["doors"],))

        # Seating capacity maps to column `seats`
        if filters.get("seating_capacity"):
            add_condition("seats = ?", (filters["seating_capacity"],))

        # State filter (optional, for state-specific searches)
        if filters.get("state"):
            add_condition("UPPER(dealer_state) = ?", (filters["state"].upper(),))

        # Search radius filter (requires user location - lat/long from browser OR ZIP lookup)
        if filters.get("search_radius") and user_latitude is not None and user_longitude is not None:
            radius_miles = filters["search_radius"]
            # Only include vehicles with valid lat/long coordinates
            conditions.append("dealer_latitude IS NOT NULL")
            conditions.append("dealer_longitude IS NOT NULL")
            # Add haversine distance calculation
            distance_expr = _haversine_distance_sql(user_latitude, user_longitude)
            conditions.append(f"({distance_expr}) <= {float(radius_miles)}")

        # Year range
        if filters.get("year"):
            lower, upper = _parse_numeric_range(str(filters["year"]))
            if lower is not None and upper is not None and lower == upper:
                add_condition("year = ?", (int(lower),))
            else:
                if lower is not None:
                    add_condition("year >= ?", (int(lower),))
                if upper is not None:
                    add_condition("year <= ?", (int(upper),))

        # Price range
        if filters.get("price"):
            lower, upper = _parse_numeric_range(str(filters["price"]))
            if lower is not None:
                add_condition("price >= ?", (int(lower),))
            if upper is not None:
                add_condition("price <= ?", (int(upper),))

        # Mileage range (vehicle odometer reading)
        if filters.get("mileage"):
            lower, upper = _parse_numeric_range(str(filters["mileage"]))
            if lower is not None:
                add_condition("mileage >= ?", (int(lower),))
            if upper is not None:
                add_condition("mileage <= ?", (int(upper),))

        # Highway MPG range (fuel economy)
        if filters.get("highway_mpg"):
            mpg_value = str(filters["highway_mpg"])
            if "-" in mpg_value:
                # Range specified (e.g., "30-40")
                lower, upper = _parse_numeric_range(mpg_value)
                if lower is not None:
                    add_condition("build_highway_mpg >= ?", (int(lower),))
                if upper is not None:
                    add_condition("build_highway_mpg <= ?", (int(upper),))
            else:
                # Single value means minimum (e.g., "35" means >= 35)
                try:
                    min_mpg = int(mpg_value)
                    add_condition("build_highway_mpg >= ?", (min_mpg,))
                except ValueError:
                    pass  # Invalid value, skip filter

        # New vs Used filter
        if filters.get("is_used") is not None:
            is_used_value = 1 if filters["is_used"] else 0
            is_used_col = _normalized_column_expr("is_used")
            add_condition(f"{is_used_col} = ?", (is_used_value,))

        # Certified Pre-Owned filter
        if filters.get("is_cpo") is not None:
            is_cpo_value = 1 if filters["is_cpo"] else 0
            add_condition("is_cpo = ?", (is_cpo_value,))

        # Avoid vehicles filter (EXCLUDE specific make/model combinations)
        avoid_vehicles = filters.get("avoid_vehicles", [])
        if avoid_vehicles:
            for avoid in avoid_vehicles:
                avoid_make = avoid.get("make")
                avoid_model = avoid.get("model")

                if avoid_make and avoid_model:
                    # Exclude specific make+model combination
                    add_condition(
                        "NOT (UPPER(make) = ? AND UPPER(model) = ?)",
                        (avoid_make.upper(), avoid_model.upper())
                    )
                elif avoid_make:
                    # Exclude entire make (all models)
                    add_condition("UPPER(make) != ?", (avoid_make.upper(),))

        # Require price and mileage to be present and valid (filter out NULL and zero values)
        conditions.append("price IS NOT NULL AND price > 0")
        conditions.append("mileage IS NOT NULL")

        # Require photos if configured
        if self.require_photos:
            conditions.append(
                "(COALESCE(photo_count, 0) > 0 OR primary_image_url IS NOT NULL)"
            )

        where_clause = ""
        if conditions:
            where_clause = " WHERE " + " AND ".join(conditions)

        # Handle ORDER BY clause
        order_clause = ""
        if order_by is not None:
            order_by_lower = order_by.lower() if isinstance(order_by, str) else "price"
            if order_by_lower in ("random", "random()"):
                order_clause = " ORDER BY RANDOM()"
            elif "," in order_by_lower:
                # Compound ordering like "year DESC, price ASC"
                # Validate and build compound order clause
                allowed_columns = {"price", "mileage", "year"}
                order_parts = []
                for part in order_by_lower.split(","):
                    part = part.strip()
                    tokens = part.split()
                    col = tokens[0] if tokens else "price"
                    dir_token = tokens[1].upper() if len(tokens) > 1 else "ASC"
                    if col in allowed_columns and dir_token in ("ASC", "DESC"):
                        order_parts.append(f"{col} {dir_token}")
                if order_parts:
                    order_clause = f" ORDER BY {', '.join(order_parts)}, vin ASC"
                else:
                    order_clause = " ORDER BY price ASC, vin ASC"
            else:
                order_column = {
                    "price": "price",
                    "mileage": "mileage",
                    "year": "year",
                }.get(order_by_lower, "price")
                direction = "DESC" if order_dir.upper() == "DESC" else "ASC"
                order_clause = f" ORDER BY {order_column} {direction}, vin ASC"

        # Handle LIMIT clause
        limit_clause = ""
        if limit is not None:
            limit_clause = f" LIMIT ? OFFSET ?"
            limit_params = [limit, offset]
        else:
            limit_params = []

        # Build final SQL with optional window function for diversity
        if max_per_make_model is not None:
            # Use window function to limit vehicles per make/model combination
            # Build window ORDER BY clause (similar logic to main order_clause)
            order_by_lower = order_by.lower() if order_by else "price"
            allowed_columns = {"price", "mileage", "year"}
            if "," in order_by_lower:
                # Compound ordering
                window_order_parts = []
                for part in order_by_lower.split(","):
                    part = part.strip()
                    tokens = part.split()
                    col = tokens[0] if tokens else "price"
                    dir_token = tokens[1].upper() if len(tokens) > 1 else "ASC"
                    if col in allowed_columns and dir_token in ("ASC", "DESC"):
                        window_order_parts.append(f"{col} {dir_token}")
                window_order_expr = ", ".join(window_order_parts) if window_order_parts else "price ASC"
            else:
                order_column = allowed_columns & {order_by_lower} and order_by_lower or "price"
                direction = "DESC" if order_dir.upper() == "DESC" else "ASC"
                window_order_expr = f"{order_column} {direction}"

            sql = f"""
            WITH ranked_vehicles AS (
                SELECT raw_json, price, mileage, primary_image_url, photo_count,
                    year, make, model, trim, body_style, drivetrain, engine, fuel_type, transmission,
                    doors, seats, exterior_color, interior_color,
                    dealer_name, dealer_city, dealer_state, dealer_zip, dealer_latitude, dealer_longitude,
                    is_used, is_cpo, vdp_url, carfax_url, vin, build_city_mpg, build_highway_mpg,
                    norm_body_type, norm_fuel_type, norm_is_used,
                    ROW_NUMBER() OVER (
                        PARTITION BY make, model
                        ORDER BY {window_order_expr}, vin ASC
                    ) as row_num
                FROM unified_vehicle_listings{where_clause}
            )
            SELECT raw_json, price, mileage, primary_image_url, photo_count,
                year, make, model, trim, body_style, drivetrain, engine, fuel_type, transmission,
                doors, seats, exterior_color, interior_color,
                dealer_name, dealer_city, dealer_state, dealer_zip, dealer_latitude, dealer_longitude,
                is_used, is_cpo, vdp_url, carfax_url, vin, build_city_mpg, build_highway_mpg,
                norm_body_type, norm_fuel_type, norm_is_used
            FROM ranked_vehicles
            WHERE row_num <= ?{order_clause}{limit_clause}
            """
            params.extend([max_per_make_model] + limit_params)
        else:
            # Standard query without window function
            sql = f"{select_clause}{where_clause}{order_clause}{limit_clause}"
            params.extend(limit_params)

        return sql, tuple(params)

    @staticmethod
    def _row_to_payload(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        """Convert a SQLite row into the payload expected downstream."""
        if not row:
            return None

        raw_json = row["raw_json"]
        if raw_json:
            try:
                payload = json.loads(raw_json)
            except json.JSONDecodeError:
                logger.warning("Failed to parse raw_json for row")
                payload = {}
        else:
            payload = {}

        # Check if this is the new unified format (flat structure) vs old Auto.dev format (nested)
        # New format has fields like "heading", "data_source" at root level
        # Old format has "vehicle" and "retailListing" as nested objects
        is_unified_format = "data_source" in payload or ("vehicle" not in payload and "retailListing" not in payload)

        if is_unified_format:
            # Transform unified format to Auto.dev format expected by downstream code
            # Prefer database columns over raw_json values (database columns are normalized)
            vehicle_data = {
                "vin": row["vin"],
                "year": row["year"],
                "make": row["make"],
                "model": row["model"],
                "trim": row["trim"],
                "price": row["price"],  # Also in retailListing for compatibility
                "mileage": row["mileage"],  # Also in retailListing as "miles"
                "bodyStyle": row["body_style"],
                "drivetrain": row["drivetrain"],
                "engine": row["engine"],
                "fuel": row["fuel_type"],
                "transmission": row["transmission"],
                "doors": row["doors"],
                "seats": row["seats"],
                "exteriorColor": row["exterior_color"],
                "interiorColor": row["interior_color"],
                "build_city_mpg": row["build_city_mpg"],
                "build_highway_mpg": row["build_highway_mpg"],
                "norm_body_type": row["norm_body_type"],
                "norm_fuel_type": row["norm_fuel_type"],
                "norm_is_used": row["norm_is_used"],
            }

            # Extract retail listing info
            retail_data = {
                "price": row["price"],
                "miles": row["mileage"],
                "dealer": row["dealer_name"],
                "city": row["dealer_city"],
                "state": row["dealer_state"],
                "zip": row["dealer_zip"],
                "vdp": row["vdp_url"],
                "carfaxUrl": row["carfax_url"],
                "primaryImage": row["primary_image_url"],
                "photoCount": row["photo_count"],
                "used": row["is_used"] if row["is_used"] is not None else True,
                "cpo": row["is_cpo"] if row["is_cpo"] is not None else False,
            }

            # Reconstruct in Auto.dev format
            transformed_payload = {
                "@id": payload.get("id", f"unified/{row['vin']}"),
                "vin": row["vin"],
                "online": payload.get("online", True),
                "vehicle": vehicle_data,
                "retailListing": retail_data,
                "wholesaleListing": None,
            }

            # Keep original payload as metadata
            transformed_payload["_original"] = payload

            return transformed_payload
        else:
            # Old Auto.dev format - use existing logic
            retail_listing = payload.setdefault("retailListing", {})
            if row["price"] is not None:
                retail_listing.setdefault("price", row["price"])
            if row["mileage"] is not None:
                retail_listing.setdefault("miles", row["mileage"])

            # Backfill photo hint if missing
            if row["primary_image_url"] and not retail_listing.get("primaryImage"):
                retail_listing["primaryImage"] = row["primary_image_url"]
            if row["photo_count"] is not None and not retail_listing.get("photoCount"):
                retail_listing["photoCount"] = row["photo_count"]

            # Add price/mileage to vehicle section for unified access
            vehicle = payload.setdefault("vehicle", {})
            if row["price"] is not None:
                vehicle.setdefault("price", row["price"])
            if row["mileage"] is not None:
                vehicle.setdefault("mileage", row["mileage"])

            # Add MPG data to vehicle section if available
            if row["build_city_mpg"] is not None:
                vehicle.setdefault("build_city_mpg", row["build_city_mpg"])
            if row["build_highway_mpg"] is not None:
                vehicle.setdefault("build_highway_mpg", row["build_highway_mpg"])

            # Add normalized columns to vehicle section
            if row["norm_body_type"] is not None:
                vehicle["norm_body_type"] = row["norm_body_type"]
            if row["norm_fuel_type"] is not None:
                vehicle["norm_fuel_type"] = row["norm_fuel_type"]
            if row["norm_is_used"] is not None:
                vehicle["norm_is_used"] = row["norm_is_used"]

            return payload
