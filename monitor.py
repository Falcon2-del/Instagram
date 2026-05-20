import os
import time
import random
import smtplib
import requests
import base64
from datetime import datetime, timedelta, timezone
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
    """Скачивает данные из Gist, включая метку времени последней синхронизации подписок"""
    print("Загрузка данных из Gist...")
    response = requests.get(GIST_API_URL, headers=HEADERS)
    response.raise_for_status()
    files = response.json().get("files", {})
    
    accounts = files.get("accounts.txt", {}).get("content", "").splitlines()
    sent_posts = set(files.get("sent_posts.txt", {}).get("content", "").splitlines())
    session_base64 = files.get("session_data.data", {}).get("content", "")
    last_sync_str = files.get("last_sync.txt", {}).get("content", "").strip()
    
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
    return accounts, sent_posts, last_sync_str

def save_all_to_gist(sent_posts_set, accounts_list=None, last_sync_str=None, update_session=False):
    """Обновляет базу постов, список аккаунтов, время синхронизации и сессию в Gist"""
    data = {"files": {}}
    
    posts_content = "\n".join(sorted(list(sent_posts_set)))
    data["files"]["sent_posts.txt"] = {"content": posts_content}
    
    if accounts_list is not None:
        accounts_content = "\n".join(sorted(accounts_list))
        data["files"]["accounts.txt"] = {"content": accounts_content}
        
    if last_sync_str is not None:
        data["files"]["last_sync.txt"] = {"content": last_sync_str}
    
    if update_session and os.path.exists(SESSION_FILENAME):
        try:
            with open(SESSION_FILENAME, "rb") as f:
                session_base64 = base64.b64encode(f.read()).decode('utf-8')
            data["files"]["session_data.data"] = {"content": session_base64}
            print("Подготовка к обновлению сессии (в формате Base64) в Gist...")
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
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465) 
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        server.close()
        print(f"Письмо успешно отправлено с темой: {subject}")
    except Exception as e:
        print(f"Ошибка при отправке почты: {e}")

def main():
    accounts, sent_posts, last_sync_str = get_gist_data()
    
    L = instaloader.Instaloader()
    
    # Имитируем поведение реального браузера, чтобы снизить вероятность блокировок 400/429
    L.context._session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    session_updated = False

    # Шаг 1: Авторизация по сессии из Gist
    if os.path.exists(SESSION_FILENAME):
        try:
            L.load_session_from_file(INSTA_USER, filename=SESSION_FILENAME)
            print(f"Успешный вход в Instagram для @{INSTA_USER} ПО СЕССИИ.")
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
        try:
            print("Файл сессии отсутствует. Выполняем первичный вход по паролю...")
            L.login(INSTA_USER, INSTA_PASSWORD)
            L.save_session_to_file(filename=SESSION_FILENAME)
            session_updated = True
            print("Первичный вход выполнен. Новая сессия сохранена локально.")
        except Exception as e:
            print(f"Критическая ошибка первичного входа по паролю: {e}")
            return

    # Шаг 2: Проверка времени последнего обновления списка подписок (раз в 24 часа)
    should_sync_followees = False
    current_time = datetime.now(timezone.utc).replace(tzinfo=None)
    
    if not last_sync_str:
        print("Синхронизация подписок еще ни разу не проводилась.")
        should_sync_followees = True
    else:
        try:
            last_sync_dt = datetime.strptime(last_sync_str, "%Y-%m-%d %H:%M:%S")
            if current_time - last_sync_dt >= timedelta(hours=24):
                print("С момента последнего обновления подписок прошло более 24 часов.")
                should_sync_followees = True
            else:
                print(f"Используем текущую базу аккаунтов. До обновления подписок осталось: {timedelta(hours=24) - (current_time - last_sync_dt)}")
        except ValueError:
            print("Ошибка чтения даты из Gist, запускаем принудительную синхронизацию подписок.")
            should_sync_followees = True

    accounts_updated = False
    new_sync_time_str = None

    if should_sync_followees:
        print(f"Начинаем сбор подписок аккаунта @{INSTA_USER}...")
        try:
            profile = instaloader.Profile.from_username(L.context, INSTA_USER)
            new_accounts = []
            
            # Альтернативный сбор подписок без "тяжелых" GraphQL запросов
            for followee in profile.get_followees():
                new_accounts.append(followee.username)
                time.sleep(random.uniform(0.5, 1.5)) # Защитная пауза
                
            print(f"Успешно собрано подписок: {len(new_accounts)}")
            
            if new_accounts:
                accounts = new_accounts
                accounts_updated = True
                new_sync_time_str = current_time.strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:
            print(f"Не удалось обновить список подписок из-за ошибки: {e}.")
            print("Скрипт продолжит работу, используя сохраненный ранее список accounts.txt из Gist.")

    if not accounts:
        print("Список аккаунтов пуст, и в Gist нет сохраненной базы. Завершение работы.")
        if session_updated:
            save_all_to_gist(sent_posts, update_session=True)
        return

    new_posts_found = False

    # Шаг 3: Основной цикл проверки постов
    for username in accounts:
        print(f"Проверяем профиль: {username}")
        try:
            profile = instaloader.Profile.from_username(L.context, username)
            
            # Используем безопасный итератор постов, оптимизированный под ограничения хостингов
            posts_iterator = profile.get_posts()
            
            for count in range(3):
                try:
                    post = next(posts_iterator)
                except StopIteration:
                    break
                except Exception as post_fetch_err:
                    print(f"Не удалось загрузить пост #{count+1} для {username}: {post_fetch_err}")
                    break
                
                post_id = str(post.mediaid)
                if post_id not in sent_posts:
                    post_url = f"https://instagram.com/p/{post.shortcode}"
                    caption = post.caption if post.caption else "[Без описания]"
                    
                    local_time = post.date_utc + timedelta(hours=5)
                    formatted_time = local_time.strftime("%d.%m.%Y %H:%M:%S")
                    
                    media_url = post.video_url if post.is_video else post.url
                    attachment_str = f"Вложение (прямая ссылка): {media_url}" if media_url else "[Медиа недоступно]"
                    
                    subject = "Instagram"
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
            print(f"Пропуск аккаунта {username} из-за ошибки доступа: {e}")
        
        # Рандомная задержка для имитации действий человека
        time.sleep(random.randint(20, 45))

    # Шаг 4: Обновление данных в Gist по мере изменений
    if new_posts_found or session_updated or accounts_updated:
        save_all_to_gist(
            sent_posts=sent_posts, 
            accounts_list=accounts if accounts_updated else None,
            last_sync_str=new_sync_time_str,
            update_session=session_updated
        )
    else:
        print("Проверка завершена. Новых постов не найдено, база в Gist актуальна.")

if __name__ == "__main__":
    main()
