# github_sync.py
import sqlite3
import json
import requests
import base64
import os
from datetime import datetime

# ===== НАСТРОЙКИ (ЗАМЕНИТЕ) =====
GITHUB_TOKEN = "your_github_token"
GITHUB_REPO = "yourusername/jarvis-keys"
GITHUB_KEYS_PATH = "keys.json"

def get_db():
    conn = sqlite3.connect('jarvis_shop.db')
    conn.row_factory = sqlite3.Row
    return conn

def sync_to_github():
    """Синхронизация всех ключей с GitHub"""
    try:
        # Получаем ключи из БД
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT license_keys.*, orders.user_id 
            FROM license_keys 
            LEFT JOIN orders ON license_keys.order_id = orders.order_id
        ''')
        keys = cursor.fetchall()
        conn.close()
        
        # Формируем JSON
        keys_data = {"keys": {}, "last_update": datetime.now().isoformat()}
        
        for key in keys:
            keys_data["keys"][key['key']] = {
                "hwid": key['hwid'] or "",
                "activations": key['activations'],
                "max_activations": key['max_activations'],
                "user_id": key['user_id'],
                "created_at": key['created_at'],
                "activated_at": key['activated_at'] or ""
            }
        
        # Сохраняем локально
        with open('keys.json', 'w', encoding='utf-8') as f:
            json.dump(keys_data, f, ensure_ascii=False, indent=2)
        
        # Отправляем в GitHub
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_KEYS_PATH}"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        # Получаем текущий файл
        get_response = requests.get(url, headers=headers)
        
        # Кодируем новый файл
        with open('keys.json', 'rb') as f:
            content = base64.b64encode(f.read()).decode('utf-8')
        
        # Данные для коммита
        data = {
            "message": f"Sync keys {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "content": content,
            "branch": "main"
        }
        
        # Добавляем sha если файл существует
        if get_response.status_code == 200:
            data["sha"] = get_response.json()['sha']
        
        # Отправляем
        put_response = requests.put(url, headers=headers, json=data)
        
        if put_response.status_code in [200, 201]:
            print(f"✅ Synced {len(keys)} keys to GitHub")
            return True
        else:
            print(f"❌ GitHub error: {put_response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Sync error: {e}")
        return False

if __name__ == '__main__':
    sync_to_github()