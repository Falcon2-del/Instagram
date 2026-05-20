import os
import time
import random
import smtplib
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import instaloader

# --- НАСТРОЙКИ ИЗ ОКРУЖЕНИЯ ---
GIST_ID = os.getenv("GIST_ID")
GIST_TOKEN = os.getenv("GIST_TOKEN")
INSTA_USER = os.getenv("INSTA_USER")
INSTA_PASSWORD = os.getenv("INSTA_PASSWORD")
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")

GIST_API_URL = f"https://api.github.com/gists/{GIST_ID}"
HEADERS = {"Authorization": f"token {GIST_TOKEN}", "Accept": "application/vnd.github.v3+json"}

SESSION_FILENAME = f"session-{INSTA_USER}"

def get_gist_data():
    """Скачивает accounts.txt, sent_posts.txt и session_data.data из Gist"""
    print("Загрузка данных из Gist...")
    response = requests.get(GIST_API_URL, headers=HEADERS)
    response.raise_for_status()
    files = response.json().get("files", {})
    
    accounts = files.get("accounts.txt", {}).get("content", "").splitlines()
    sent_posts = set(files.get("sent_posts.txt", {}).get("content", "").splitlines())
    session_content = files.get("session_data.data", {}).get("content", "")
    
    # Восстанавливаем файл сессии на диск виртуальной машины, если он есть в Gist
    if session_content and session_content.strip() != "empty":
        with open(SESSION_FILENAME, "w", encoding="utf-8") as f:
            f.write(session_content)
        print("Файл сессии успешно скачан из Gist и воссоздан локально.")
    else:
        print("В Gist пока нет сохраненной сессии.")
        
    accounts = [a.strip() for a in accounts if a.strip()]
    return accounts, sent_posts

def save_all_to_gist(sent_posts_set, update_session=False):
    """Обновляет базу постов и (опционально) сессию в Gist одним запросом"""
    posts_content = "\n".join(sorted(list(sent_posts_set)))
    
    data = {
        "files": {
            "sent_posts.txt": {"content": posts_content}
        }
    }
    
    # Если нужно обновить файл сессии в Gist
    if update_session and os.path.exists(SESSION_FILENAME):
        with open(SESSION_FILENAME, "r", encoding="utf-8") as f:
            session_content = f.read()
        data["files"]["session_data.data"] = {"content": session_content}
        print("Подготовка к обновлению сессии в Gist...")

    response = requests.patch(GIST_API_URL, headers=HEADERS, json=data)
    response.raise_for_status()
    print("Данные в Gist успешно обновлены.")

def send_email(subject, body):
    """Отправка уведомления на почту"""
    msg = MIMEMultipart()
    msg['From'] = EMAIL_SENDER
    msg['To'] = EMAIL_RECEIVER
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))
    
    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465) # Смените на хост вашего провайдера, если не Gmail
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        server.close()
        print(f"Письмо отправлено: {subject}")
    except Exception as e:
        print(f"Ошибка отправки почты: {e}")

def main():
    accounts, sent_posts = get_gist_data()
    
    if not accounts:
        print("Список подписок в accounts.txt пуст.")
        return

    L = instaloader.Instaloader()
    session_updated = False

    # Попытка 1: Вход через локальный файл сессии, который мы только что скачали из Gist
    if os.path.exists(SESSION_FILENAME):
        try:
            L.load_session_from_file(INSTA_USER, filename=SESSION_FILENAME)
            print("Успешный вход в Instagram ПО СЕССИИ (без пароля).")
        except Exception as e:
            print(f"Сессия из Gist оказалась невалидной ({e}). Пробуем войти по паролю...")
            try:
                L.login(INSTA_USER, INSTA_PASSWORD)
                L.save_session_to_file(filename=SESSION_FILENAME)
                session_updated = True
                print("Успешный вход по паролю. Сгенерирована новая сессия.")
            except Exception as login_err:
                print(f"Критическая ошибка входа по паролю: {login_err}")
                return
    else:
        # Попытка 2: Если файла сессии вообще не было в Gist (первый запуск)
        try:
            L.login(INSTA_USER, INSTA_PASSWORD)
            L.save_session_to_file(filename=SESSION_FILENAME)
            session_updated = True
            print("Первый вход выполнен по паролю. Сессия сохранена.")
        except Exception as e:
            print(f"Критическая ошибка первого входа по паролю: {e}")
            return

    new_posts_found = False

    # Основной цикл обхода аккаунтов
    for username in accounts:
        print(f"Проверяем профиль: {username}")
        try:
            profile = instaloader.Profile.from_username(L.context, username)
            
            # Берем последние 3 поста
            for count, post in enumerate(profile.get_posts()):
                if count >= 3:
                    break
                
                post_id = str(post.mediaid)
                if post_id not in sent_posts:
                    post_url = f"https://instagram.com/p/{post.shortcode}"
                    caption = post.caption if post.caption else "[Без описания]"
                    
                    subject = f"Новый пост от {username}"
                    body = f"Пользователь @{username} выложил новый пост!\n\nСсылка: {post_url}\n\nОписание:\n{caption}"
                    
                    send_email(subject, body)
                    
                    sent_posts.add(post_id)
                    new_posts_found = True
                    
        except Exception as e:
            print(f"Не удалось проверить аккаунт {username}: {e}")
        
        # Рандомная задержка между профилями
        time.sleep(random.randint(15, 35))

    # Сохраняем результаты работы, если что-то изменилось
    if new_posts_found or session_updated:
        save_all_to_gist(sent_posts, update_session=session_updated)
    else:
        print("Никаких обновлений не обнаружено. Завершение работы.")

if __name__ == "__main__":
    main()
