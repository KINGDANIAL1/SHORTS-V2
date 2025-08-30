import os
import random
import time
import tempfile
import schedule
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

# ====================== إعدادات Google API ======================
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/drive.readonly"
]
TOKEN_FILE = "token.json"
POSTED_LOG = "posted_from_drive.txt"

# ====================== خوارزمية العناوين ======================
KEYWORDS = ["نجاح", "تحفيز", "ريادة أعمال", "مال", "أسرار النجاح", "تطوير الذات"]
HOOKS = [
    "🚀 سر لا يعرفه الكثير:",
    "❌ أكبر خطأ يمنعك من:",
    "🔥 السر وراء",
    "💡 كيف تبدأ بـ",
    "🧠 عقلية:"
]

def generate_title():
    hook = random.choice(HOOKS)
    keyword = random.choice(KEYWORDS)
    return f"{hook} {keyword} #Shorts"

# ====================== خوارزمية الوصف ======================
DEFAULT_HASHTAGS = ["#Shorts", "#تحفيز", "#نجاح", "#تطوير_الذات", "#ريادة_أعمال"]

def generate_description():
    keyword = random.choice(KEYWORDS)
    hashtags = " ".join(DEFAULT_HASHTAGS)
    return f"""
{keyword} في أقل من دقيقة! 🚀
📌 لا تنسَ الاشتراك لمزيد من الفيديوهات القصيرة الملهمة.
✅ اكتب في التعليقات: ما رأيك في هذه النصيحة؟

{hashtags}
"""

# ====================== خدمات Google ======================
def get_youtube_service():
    token_json = os.getenv("TOKEN_JSON")
    if not token_json:
        raise Exception("❌ يرجى توفير TOKEN_JSON كمتغير بيئي")
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".json", delete=False) as tmp_file:
        tmp_file.write(token_json)
        tmp_file.flush()
        tmp_path = tmp_file.name
    creds = Credentials.from_authorized_user_file(tmp_path, SCOPES)
    os.remove(tmp_path)
    return build("youtube", "v3", credentials=creds)

def get_drive_service():
    service_account_json = os.getenv("SERVICE_ACCOUNT_JSON")
    if not service_account_json:
        raise Exception("❌ يرجى ضبط SERVICE_ACCOUNT_JSON.")
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".json", delete=False) as tmp_file:
        tmp_file.write(service_account_json)
        tmp_file.flush()
        tmp_path = tmp_file.name
    credentials = service_account.Credentials.from_service_account_file(tmp_path, scopes=SCOPES)
    os.remove(tmp_path)
    return build("drive", "v3", credentials=credentials)

# ====================== إدارة الملفات ======================
def load_posted():
    if not os.path.exists(POSTED_LOG):
        return set()
    with open(POSTED_LOG, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f.readlines())

def save_posted(filename):
    with open(POSTED_LOG, "a", encoding="utf-8") as f:
        f.write(filename + "\n")

def get_videos_from_drive(service):
    query = "mimeType contains 'video/' and trashed = false"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    return results.get("files", [])

def download_video(service, file):
    request = service.files().get_media(fileId=file["id"])
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        downloader = MediaIoBaseDownload(tmp, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return tmp.name

# ====================== رفع الفيديو ======================
def upload_video_to_youtube(youtube, file_path, title, description, tags=[]):
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": [tag.strip("#") for tag in tags],
            "categoryId": "22"
        },
        "status": {
            "privacyStatus": "public"
        }
    }

    media = MediaIoBaseUpload(open(file_path, "rb"), mimetype="video/*", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = request.execute()
    print(f"✅ تم النشر على يوتيوب: https://youtu.be/{response['id']}")

# ====================== مهمة نشر فيديو ======================
def publish_youtube_short(youtube, drive, file):
    tmp_path = download_video(drive, file)
    try:
        title = generate_title()
        description = generate_description()
        upload_video_to_youtube(youtube, tmp_path, title, description, DEFAULT_HASHTAGS)
        save_posted(file["name"])
        time.sleep(10)  # تأخير حتى يحلل يوتيوب الفيديو
    except Exception as e:
        print(f"❌ فشل النشر: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

# ====================== تشغيل الجدولة ======================
def main():
    print("🔐 تسجيل الدخول إلى YouTube و Google Drive...")
    youtube = get_youtube_service()
    drive = get_drive_service()
    posted = load_posted()

    def job():
        all_files = get_videos_from_drive(drive)
        available = [f for f in all_files if f["name"].endswith(".mp4") and f["name"] not in posted]

        if not available:
            print("🚫 لا توجد فيديوهات جديدة")
            return
        random.shuffle(available)
        publish_youtube_short(youtube, drive, available[0])

    # أوقات النشر المثالية
    schedule.every().day.at("11:00").do(job)
    schedule.every().day.at("14:00").do(job)
    schedule.every().day.at("18:00").do(job)
    schedule.every().day.at("21:00").do(job)

    print("⏰ السكربت يعمل تلقائيًا...")
    try:
        while True:
            schedule.run_pending()
            time.sleep(60)
    except KeyboardInterrupt:
        print("🛑 تم إيقاف السكربت.")

if __name__ == "__main__":
    main()
