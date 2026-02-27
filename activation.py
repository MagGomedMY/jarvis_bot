# activation.py
import requests
import hashlib
import json
import os
import platform
import subprocess
from datetime import datetime

class JarvisActivator:
    def __init__(self):
        # URL вашего сервера активации (Render/Heroku)
        self.server_url = "https://your-server.onrender.com"
        # GitHub raw URL для проверки (запасной вариант)
        self.github_url = "https://raw.githubusercontent.com/yourusername/jarvis-keys/main/keys.json"
        self.key_file = "jarvis.lic"
        
    def get_hwid(self):
        """Получение уникального ID компьютера"""
        try:
            # Для Windows
            if platform.system() == "Windows":
                output = subprocess.check_output(
                    'wmic diskdrive where index=0 get serialnumber',
                    shell=True
                ).decode().split('\n')[1].strip()
                
                computer_name = subprocess.check_output(
                    'hostname', shell=True
                ).decode().strip()
                
                hwid_string = output + computer_name + platform.processor()
                
            # Для других ОС
            else:
                import uuid
                hwid_string = str(uuid.getnode()) + platform.node()
            
            # Хешируем
            return hashlib.sha256(hwid_string.encode()).hexdigest()
            
        except Exception as e:
            # Fallback
            import uuid
            return hashlib.sha256(str(uuid.getnode()).encode()).hexdigest()
    
    def save_activation(self, key):
        """Сохранение активации локально"""
        try:
            data = {
                "key": key,
                "hwid": self.get_hwid(),
                "activated_at": datetime.now().isoformat()
            }
            with open(self.key_file, 'w') as f:
                json.dump(data, f)
            return True
        except:
            return False
    
    def load_activation(self):
        """Загрузка сохранённой активации"""
        try:
            if os.path.exists(self.key_file):
                with open(self.key_file, 'r') as f:
                    data = json.load(f)
                return data
        except:
            pass
        return None
    
    def activate(self, key):
        """Активация ключа"""
        hwid = self.get_hwid()
        
        try:
            # Пробуем активировать через сервер
            response = requests.post(
                f"{self.server_url}/activate",
                json={"key": key, "hwid": hwid},
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    self.save_activation(key)
                    return True, result.get('message', 'Активация успешна')
                else:
                    return False, result.get('message', 'Ошибка активации')
            else:
                return False, "Сервер не отвечает"
                
        except Exception as e:
            # Если сервер недоступен, пробуем GitHub
            try:
                response = requests.get(self.github_url, timeout=5)
                if response.status_code == 200:
                    keys_data = response.json()
                    
                    if key in keys_data['keys']:
                        key_info = keys_data['keys'][key]
                        
                        if not key_info['hwid']:
                            return False, "Требуется подключение к серверу для первой активации"
                        elif key_info['hwid'] == hwid:
                            self.save_activation(key)
                            return True, "Активация подтверждена"
                        else:
                            return False, "Ключ активирован на другом компьютере"
                    else:
                        return False, "Ключ не найден"
                else:
                    return False, "Не удалось проверить ключ"
                    
            except:
                return False, "Ошибка подключения к серверу активации"
    
    def check(self):
        """Проверка статуса активации"""
        # Проверяем локальный файл
        activation = self.load_activation()
        if not activation:
            return False, "Активация не найдена"
        
        # Проверяем HWID
        if activation['hwid'] != self.get_hwid():
            return False, "HWID изменился"
        
        # Проверяем через сервер
        try:
            response = requests.post(
                f"{self.server_url}/check",
                json={"key": activation['key'], "hwid": activation['hwid']},
                timeout=5
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('valid'):
                    return True, f"Активировано ({result.get('activations', 1)}/{result.get('max_activations', 2)})"
                else:
                    return False, "Ключ недействителен"
            else:
                # Если сервер недоступен, доверяем локальной проверке
                return True, "Локальная активация"
                
        except:
            # Если нет интернета, доверяем локальной
            return True, "Офлайн режим"