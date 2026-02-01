import os
import psycopg2
import logging
from fastapi import HTTPException
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DB_CONFIG = {
    'host': os.getenv("DB_HOST"),
    'port': os.getenv("DB_PORT"),
    'database': os.getenv("DB_NAME"),
    'user': os.getenv("DB_USER"),
    'password': os.getenv("DB_PASSWORD")
}

class Database:
    def __init__(self):
        self.connection = None

    def get_connection(self):
        try:
            if self.connection is None or self.connection.closed:
                self.connection = psycopg2.connect(**DB_CONFIG)
                self.connection.autocommit = False
            return self.connection
        except Exception as e:
            logger.error(f"Database connection error: {e}")
            raise HTTPException(status_code=500, detail="Database connection failed")

    def close_connection(self):
        if self.connection and not self.connection.closed:
            self.connection.close()
            self.connection = None

db = Database()
