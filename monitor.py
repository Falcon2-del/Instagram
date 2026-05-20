import os
import time
import random
import smtplib
import requests
import base64
from datetime import timedelta
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
    session_base64 = files.get("session_data.data", {}).get("content", "")
    
    # Декодируем Base64 обратно в бинарный файл сессии на диск
    if session_base64 and session_base64.strip() not in ["empty", ""]:
        try:
            binary_session = base64.b64decode(session_base64.encode('utf-8'))
            with open(SESSION_FILENAME, "wb") as f:
                f.write(binary_session)
            print("Файл сессии успешно декодирован из Base64 и воссоздан локально.")
        except Exception as e:
            print(f"Ошибка декодирования сессии из Gist: {e}")
    else:
        print("В Gist пока нет сохраненной сессии или файл пуст.")
        
    accounts = [a.strip() for a in accounts if a.strip()]
    return accounts, sent_posts

def save_all_to_gist(sent_posts_set, update_session=False):
    """Обновляет базу постов и (опционально) кодирует/сохраняет сессию в Gist"""
    posts_content = "\n".join(sorted(list(sent_posts_set)))
    
    data = {
        "files": {
            "sent_posts.txt": {"content": posts_content}
        }
    }
    
    # Если сессия обновилась, кодируем бинарный файл в Base64 текст для Gist
    if update_session and os.path.exists(SESSION_FILENAME):
        try:
            with open(SESSION_FILENAME, "rb") as f:
                session_base64 = base64.b64encode(f.read()).decode('utf-8')
            data["files"]["session_data.data"] = {"content": session_base64}
            print("Подготовка к更新сессии (в формате Base64) в Gist...")
        except Exception as e:
            print(f"Не удалось подготовить сессию для отправки: {e}")

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
        # Для большинства почтовых служб (Gmail, Yandex) используется SSL на порту 465
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465) 
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        server.close()
        print(f"Письмо успешно отправлено с темой: {subject}")
    except Exception as e:
        print(f"Ошибка при отправке почты: {e}")

def main():
    accounts, sent_posts = get_gist_data()
    
    if not accounts:
        print("Список подписок в accounts.txt пуст. Некого проверять.")
        return

    L = instaloader.Instaloader()
    session_updated = False

    # Шаг 1: Пробуем зайти по файлу сессии, который только что развернули из Gist
    if os.path.exists(SESSION_FILENAME):
        try:
            L.load_session_from_file(INSTA_USER, filename=SESSION_FILENAME)
            print(f"Успешный вход в Instagram для @{INSTA_USER} ПО СЕССИИ (без пароля).")
        except Exception as e:
            print(f"Сессия из Gist не подошла ({e}). Пробуем войти по логину и паролю...")
            try:
                L.login(INSTA_USER, INSTA_PASSWORD)
                L.save_session_to_file(filename=SESSION_FILENAME)
                session_updated = True
                print("Вход по паролю успешен. Создана новая сессия.")
            except Exception as login_err:
                print(f"Критическая ошибка входа по паролю: {login_err}")
                return
    else:
        # Шаг 2: Если файла сессии вообще не было в Gist (резервный сценарий)
        try:
            print("Файл сессии отсутствует. Выполняем первичный вход по паролю...")
            L.login(INSTA_USER, INSTA_PASSWORD)
            L.save_session_to_file(filename=SESSION_FILENAME)
            session_updated = True
            print("Первичный вход выполнен. Новая сессия сохранена локально.")
        except Exception as e:
            print(f"Критическая ошибка первичного входа по паролю: {e}")
            return

    new_posts_found = False

    # Шаг 3: Основной цикл проверки отслеживаемых аккаунтов
    for username in accounts:
        print(f"Проверяем профиль: {username}")
        try:
            profile = instaloader.Profile.from_username(L.context, username)
            
            # Берем последние 3 публикации, чтобы не делать лишних запросов
            for count, post in enumerate(profile.get_posts()):
                if count >= 3:
                    break
                
                post_id = str(post.mediaid)
                if post_id not in sent_posts:
                    post_url = f"https://instagram.com/p/{post.shortcode}"
                    caption = post.caption if post.caption else "[Без описания]"
                    
                    # Расчет времени публикации (Перевод из UTC в GMT+5)
                    local_time = post.date_utc + timedelta(hours=5)
                    formatted_time = local_time.strftime("%d.%m.%Y %H:%M:%S")
                    
                    # Проверяем тип контента для добавления прямой ссылки на вложение в тело
                    media_url = post.video_url if post.is_video else post.url
                    attachment_str = f"Вложение (прямая ссылка): {media_url}" if media_url else "[Медиа недоступно]"
                    
                    # Статичная тема письма
                    subject = "Instagram"
                    
                    # Формирование тела письма
                    body = (
                        f"Автор: {username}\n"
                        f"Время публикации: {formatted_time}\n"
                        f"Ссылка на пост: {post_url}\n\n"
                        f"{attachment_str}\n\n"
                        f"Описание:\n{caption}"
                    )
                    
                    send_email(subject, body)
                    
                    sent_posts.add(post_id)
                    new_posts_found = True
                    
        except Exception as e:
            print(f"Не удалось проверить аккаунт {username}: {e}")
        
        # Обязательная «человеческая» пауза между запросами к разным профилям
        time.sleep(random.randint(15, 35))

    # Шаг 4: Если база постов обновилась или сгенерировалась новая сессия — сохраняем всё в Gist
    if new_posts_found or session_updated:
        save_all_to_gist(sent_posts, update_session=session_updated)
    else:
        print("Проверка завершена. Новых публикаций не найдено.")

if __name__ == "__main__":
    main()
