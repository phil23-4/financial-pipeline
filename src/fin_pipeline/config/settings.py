import os

DB_ENDPOINT = os.getenv("SURREAL_ENDPOINT", "ws://localhost:8000/rpc")
DB_USER = os.getenv("SURREAL_USER", "root")
DB_PASS = os.getenv("SURREAL_PASS", "root")
DB_NS = os.getenv("SURREAL_NAMESPACE", "finance")
DB_DB = os.getenv("SURREAL_DATABASE", "analytics")
COMPANY_TABLE = os.getenv("COMPANY_TABLE", "company")

DB_AUTH = {
    "user": DB_USER,
    "pass": DB_PASS,
    "namespace": DB_NS,
    "database": DB_DB
}
