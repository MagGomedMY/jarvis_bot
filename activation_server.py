# activation_server.py
from flask import Flask, request, jsonify
import requests
import hashlib
import json
import os
from datetime import datetime

app = Flask(__name__)

# ===== НАСТРОЙКИ (ЗАМЕНИТЕ) =====
GITHUB_RAW_URL = "https://raw.githubusercontent.com/MagGomedMY/jarvis-keys/main/keys.json"
GITHUB_TOKEN = "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"
GITHUB_REPO = "MagGomedMY/jarvis-keys"
GITHUB_KEYS_PATH = "keys.json"

# Секретный ключ для подписи ответов
SECRET_KEY = "JarvisActivationSecretKey_2026_8f7d3a1b9c4e2f5a8d7b3c1e9f4a2d5b"

@app.route('/', methods=['GET'])
def home():
    """Главная страница для проверки работы сервера"""
    return jsonify({
        "status": "online",
        "service": "Jarvis Activation Server",
        "version": "1.0.0",
        "endpoints": {
            "GET /": "Эта информация",
            "GET /check": "Проверка статуса (для совместимости)",
            "POST /check": "Проверка ключа",
            "POST /activate": "Активация ключа"
        }
    })

@app.route('/check', methods=['GET'])
def check_get():
    """GET версия для проверки в браузере"""
    return jsonify({
        "status": "check_endpoint",
        "message": "Используйте POST запрос для проверки ключа",
        "example": {
            "method": "POST",
            "url": "/check",
            "body": {
                "key": "JARVIS-XXXX-XXXX-XXXX",
                "hwid": "ваш_hwid"
            }
        }
    })

def get_hwid_hash(hwid):
    """Хеширование HWID для безопасности"""
    return hashlib.sha256(f"{SECRET_KEY}{hwid}".encode()).hexdigest()[:16]

def update_key_in_github(key, hwid):
    """Обновление ключа в GitHub"""
    try:
        import base64
        
        # Получаем текущий файл
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_KEYS_PATH}"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        get_response = requests.get(url, headers=headers)
        
        if get_response.status_code != 200:
            return False
        
        # Декодируем текущий файл
        current_content = base64.b64decode(get_response.json()['content']).decode('utf-8')
        keys_data = json.loads(current_content)
        
        # Обновляем ключ
        if key in keys_data['keys']:
            if not keys_data['keys'][key]['hwid']:
                # Первая активация
                keys_data['keys'][key]['hwid'] = get_hwid_hash(hwid)
                keys_data['keys'][key]['activations'] = 1
                keys_data['keys'][key]['activated_at'] = datetime.now().isoformat()
            elif keys_data['keys'][key]['hwid'] == get_hwid_hash(hwid):
                # Тот же компьютер
                pass
            elif keys_data['keys'][key]['activations'] < keys_data['keys'][key]['max_activations']:
                # Новый компьютер
                keys_data['keys'][key]['hwid'] = get_hwid_hash(hwid)
                keys_data['keys'][key]['activations'] += 1
                keys_data['keys'][key]['activated_at'] = datetime.now().isoformat()
            else:
                return False
        else:
            return False
        
        # Кодируем обратно
        new_content = base64.b64encode(json.dumps(keys_data).encode()).decode('utf-8')
        
        # Отправляем обновление
        data = {
            "message": f"Activate key {key}",
            "content": new_content,
            "sha": get_response.json()['sha'],
            "branch": "main"
        }
        
        put_response = requests.put(url, headers=headers, json=data)
        
        return put_response.status_code in [200, 201]
        
    except Exception as e:
        print(f"Error updating GitHub: {e}")
        return False

@app.route('/activate', methods=['POST'])
def activate():
    """Активация ключа"""
    data = request.json
    key = data.get('key', '').upper()
    hwid = data.get('hwid', '')
    
    try:
        # Получаем ключи из GitHub
        response = requests.get(GITHUB_RAW_URL, timeout=5)
        
        if response.status_code != 200:
            return jsonify({
                'success': False,
                'error': 'SERVER_ERROR',
                'message': 'Ошибка сервера активации'
            })
        
        keys_data = response.json()
        
        if key not in keys_data['keys']:
            return jsonify({
                'success': False,
                'error': 'INVALID_KEY',
                'message': 'Ключ не найден'
            })
        
        key_info = keys_data['keys'][key]
        hwid_hash = get_hwid_hash(hwid)
        
        # Проверяем статус
        if not key_info['hwid']:
            # Первая активация
            if update_key_in_github(key, hwid):
                return jsonify({
                    'success': True,
                    'message': 'Ключ успешно активирован!',
                    'activations': 1,
                    'max_activations': key_info['max_activations']
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'UPDATE_FAILED',
                    'message': 'Ошибка активации'
                })
        
        elif key_info['hwid'] == hwid_hash:
            # Тот же компьютер
            return jsonify({
                'success': True,
                'message': 'Ключ уже активирован на этом компьютере',
                'activations': key_info['activations'],
                'max_activations': key_info['max_activations']
            })
        
        elif key_info['activations'] < key_info['max_activations']:
            # Новый компьютер
            if update_key_in_github(key, hwid):
                return jsonify({
                    'success': True,
                    'message': 'Ключ активирован на новом компьютере',
                    'activations': key_info['activations'] + 1,
                    'max_activations': key_info['max_activations']
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'UPDATE_FAILED',
                    'message': 'Ошибка активации'
                })
        
        else:
            return jsonify({
                'success': False,
                'error': 'MAX_ACTIVATIONS',
                'message': f'Ключ уже активирован на {key_info["max_activations"]} компьютерах'
            })
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': 'EXCEPTION',
            'message': str(e)
        })

@app.route('/check', methods=['POST'])
def check():
    """Проверка статуса ключа"""
    data = request.json
    key = data.get('key', '').upper()
    hwid = data.get('hwid', '')
    
    try:
        response = requests.get(GITHUB_RAW_URL, timeout=5)
        
        if response.status_code != 200:
            return jsonify({'valid': False})
        
        keys_data = response.json()
        
        if key not in keys_data['keys']:
            return jsonify({'valid': False})
        
        key_info = keys_data['keys'][key]
        hwid_hash = get_hwid_hash(hwid)
        
        if key_info['hwid'] == hwid_hash:
            return jsonify({
                'valid': True,
                'activations': key_info['activations'],
                'max_activations': key_info['max_activations']
            })
        
        return jsonify({'valid': False})
        
    except:
        return jsonify({'valid': False})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))

    app.run(host='0.0.0.0', port=port)


