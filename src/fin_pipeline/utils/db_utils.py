"""Database utility functions shared across modules."""


def quote_record_id(record_id: str) -> str:
    """Quote record IDs so hyphenated accession numbers are not parsed as subtraction.
    
    Args:
        record_id: Record ID in format "table:key" or just "table"
        
    Returns:
        Quoted record ID with angle bracket notation for the key part
    """
    table, _, key = record_id.partition(":")
    return f"{table}:⟨{key}⟩" if key else record_id
