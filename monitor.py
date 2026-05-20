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

def get_gist_data():
    """Скачивает файлы accounts.txt и sent_posts.txt из Gist"""
    response = requests.get(GIST_API_URL, headers=HEADERS)
    response.raise_for_status()
    files = response.json().get("files", {})
    
    accounts = files.get("accounts.txt", {}).get("content", "").splitlines()
    sent_posts = set(files.get("sent_posts.txt", {}).get("content", "").splitlines())
    
    # Очищаем пустые строки
    accounts = [a.strip() for a in accounts if a.strip()]
    return accounts, sent_posts

def update_gist_posts(sent_posts_set):
    """Записывает обновленный список отправленных постов обратно в Gist"""
    content = "\n".join(sorted(list(sent_posts_set)))
    data = {"files": {"sent_posts.txt": {"content": content}}}
    response = requests.patch(GIST_API_URL, headers=HEADERS, json=data)
    response.raise_for_status()

def send_email(subject, body):
    """Отправка уведомления на почту"""
    msg = MIMEMultipart()
    msg['From'] = EMAIL_SENDER
    msg['To'] = EMAIL_RECEIVER
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))
    
    # Настройки для большинства SMTP (Gmail, Yandex, Mail.ru обычно используют порт 465/SSL)
    # Если у вас Gmail/Yandex, лучше использовать SMTP_SSL на порту 465
    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465) # Замените хост, если почта не Gmail
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        server.close()
        print(f"Письмо отправлено: {subject}")
    except Exception as e:
        print(f"Ошибка отправки почты: {e}")

def main():
    print("Запуск мониторинга...")
    accounts, sent_posts = get_gist_data()
    
    if not accounts:
        print("Список подписок в accounts.txt пуст.")
        return

    # Инициализация Инстаграма
    L = instaloader.Instaloader()
    try:
        # Для облака лучше каждый раз заходить аккуратно, но в идеале позже добавить сохранение сессии в Gist
        L.login(INSTA_USER, INSTA_PASSWORD)
    except Exception as e:
        print(f"Ошибка входа в Instagram: {e}")
        return

    new_posts_found = False

    # Обходим аккаунты из файла
    for username in accounts:
        print(f"Проверяем профиль: {username}")
        try:
            profile = instaloader.Profile.from_username(L.context, username)
            
            # Берем последние 3 поста, чтобы не спамить запросами
            for count, post in enumerate(profile.get_posts()):
                if count >= 3:
                    break
                
                post_id = str(post.mediaid)
                if post_id not in sent_posts:
                    post_url = f"https://instagram.com/p/{post.shortcode}"
                    caption = post.caption if post.caption else "[Без описания]"
                    
                    # Формируем письмо
                    subject = f"Новый пост от {username}"
                    body = f"Пользователь @{username} выложил новую публикацию!\n\nСсылка: {post_url}\n\nОписание:\n{caption}"
                    
                    send_email(subject, body)
                    
                    # Запоминаем, что пост отправлен
                    sent_posts.add(post_id)
                    new_posts_found = True
                    
        except Exception as e:
            print(f"Не удалось проверить аккаунт {username}: {e}")
        
        # Случайная пауза между аккаунтами (имитируем человека)
        time.sleep(random.randint(15, 35))

    # Если появились новые посты, сохраняем базу данных в Gist
    if new_posts_found:
        print("Обновляем базу данных отправленных постов в Gist...")
        update_gist_posts(sent_posts)
    else:
        print("Новых публикаций не обнаружено.")

if __name__ == "__main__":
    main()
