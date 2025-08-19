import sqlite3
from datetime import datetime

# Initialize the database connection
conn = sqlite3.connect('scans.db')
cursor = conn.cursor()

# Create the scans table if it doesn't exist
cursor.execute('''
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    url TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    hate_detected BOOLEAN NOT NULL,
    save_path TEXT NOT NULL
)
''')

# Function to insert a new scan record
def insert_scan(username: str, url: str, hate_detected: bool, save_path: str):
    collected_at = datetime.now().isoformat()
    cursor.execute('''
    INSERT INTO scans (username, url, collected_at, hate_detected, save_path)
    VALUES (?, ?, ?, ?, ?)
    ''', (username, url, collected_at, hate_detected, save_path))
    conn.commit()

