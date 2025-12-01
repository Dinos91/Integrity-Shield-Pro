import sqlite3
from cryptography.fernet import Fernet
import os

class SecureStorage:
    def __init__(self, db_name="integrity.db", key_file="secret.key"):
        self.db_name = db_name
        self.key_file = key_file
        self.key = self._load_or_generate_key()
        self.cipher = Fernet(self.key)
        self._init_db()

    def _load_or_generate_key(self):
        if os.path.exists(self.key_file):
            with open(self.key_file, "rb") as key_file:
                return key_file.read()
        else:
            key = Fernet.generate_key()
            with open(self.key_file, "wb") as key_file:
                key_file.write(key)
            return key

    def _init_db(self):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE,
                hash_sum TEXT
            )
        ''')
        # Включаем WAL-режим для скорости и надежности
        cursor.execute('PRAGMA journal_mode=WAL;') 
        conn.commit()
        conn.close()

    def clear_database(self):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM files')
        conn.commit()
        conn.close()

    def _encrypt(self, data):
        return self.cipher.encrypt(data.encode()).decode()

    def _decrypt(self, data):
        return self.cipher.decrypt(data.encode()).decode()

    def save_batch(self, file_list):
        """Пакетная вставка для ускорения"""
        if not file_list: return
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        encrypted_data = []
        for path, h in file_list:
            encrypted_data.append((self._encrypt(path), self._encrypt(h)))
        try:
            cursor.executemany('INSERT OR REPLACE INTO files (path, hash_sum) VALUES (?, ?)', encrypted_data)
            conn.commit()
        except Exception as e:
            print(f"Ошибка БД: {e}")
        finally:
            conn.close()

    def add_or_update_file(self, path, hash_sum):
        encrypted_path = self._encrypt(path)
        encrypted_hash = self._encrypt(hash_sum)
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO files (path, hash_sum) VALUES (?, ?)', 
                       (encrypted_path, encrypted_hash))
        conn.commit()
        conn.close()

    def delete_file(self, target_path):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('SELECT id, path FROM files')
        rows = cursor.fetchall()
        id_to_delete = None
        for row in rows:
            try:
                if self._decrypt(row[1]) == target_path:
                    id_to_delete = row[0]
                    break
            except: continue
        if id_to_delete:
            cursor.execute('DELETE FROM files WHERE id = ?', (id_to_delete,))
            conn.commit()
        conn.close()

    def get_all_files(self):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('SELECT path, hash_sum FROM files')
        rows = cursor.fetchall()
        conn.close()
        decrypted_data = {}
        for r in rows:
            try:
                path = self._decrypt(r[0])
                h_sum = self._decrypt(r[1])
                decrypted_data[path] = h_sum
            except: continue 
        return decrypted_data