import os
import random
import time
import tempfile
import schedule
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

# ----------------- إعدادات -----------------
SCOPES = ['https://www.googleapis.com/auth/youtube.upload', 'https://www.googleapis.com/auth/drive.readonly']
POSTED_LOG = "posted_from_drive.txt"
THUMBNAILS_DIR = "thumbnails"  # مجلد الصور المصغرة

# كلمات رائجة وعناوين ديناميكية
TRENDING_KEYWORDS = [
    "نجاح", "تحفيز", "أفكار_مشاريع", "تطوير_ذاتي", "ريادة_أعمال", "Shorts", "تعلم_سريع", "فلوس", "ذكاء_عاطفي"
]

TITLE_TEMPLATES = [
    "🚀 {keyword} في دقيقة واحدة! #Shorts",
    "🔥 هل تعرف {keyword}؟ شاهد الآن! #Shorts",
    "💡 سر {keyword} الذي سيغير حياتك! #Shorts",
    "❌ تجنب هذا الخطأ مع {keyword}! #Shorts",
    "📈 أفضل نصائح {keyword} اليوم! #Shorts"
]

DESCRIPTION_TEMPLATES = [
    "🔥 فيديو مليء بالإلهام والنصائح حول {keyword}!\n📌 لا تنسَ الاشتراك لمزيد من الفيديوهات.\n✅ شاهد الفيديو حتى النهاية! #Shorts",
    "🚀 اكتشف أسرار {keyword} بطريقة سريعة وممتعة!\n📌 اشترك الآن لتصلك كل الفيديوهات الجديدة يوميًا. #Shorts",
]

HASHTAGS = [
    "#تحفيز", "#تعلم", "#ريادة_أعمال", "#نصائح", "#ابدأ", "#Shorts", "#نجاح", "#فلوس", "#تطوير_ذاتي"
]

# ----------------- خدمات Google -----------------
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
    return build('youtube', 'v3', credentials=creds)

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
    return build('drive', 'v3', credentials=credentials)

# ----------------- التعامل مع الملفات المنشورة -----------------
def load_posted():
    if not os.path.exists(POSTED_LOG):
        return set()
    with open(POSTED_LOG, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f.readlines())

def save_posted(filename):
    with open(POSTED_LOG, "a", encoding="utf-8") as f:
        f.write(filename + "\n")

# ----------------- جلب الفيديوهات -----------------
def get_videos_from_drive(service):
    query = "mimeType contains 'video/' and trashed = false"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    return results.get("files", [])

def download_video(service, file):
    request = service.files().get_media(fileId=file['id'])
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        downloader = MediaIoBaseDownload(tmp, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return tmp.name

# ----------------- اختيار Thumbnail -----------------
def get_random_thumbnail():
    if not os.path.exists(THUMBNAILS_DIR):
        return None
    files = [f for f in os.listdir(THUMBNAILS_DIR) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
    if not files:
        return None
    return os.path.join(THUMBNAILS_DIR, random.choice(files))

def set_thumbnail(youtube, video_id, thumbnail_path):
    try:
        ext = os.path.splitext(thumbnail_path)[1].lower()
        if ext in ['.jpg', '.jpeg']:
            mimetype = 'image/jpeg'
        elif ext == '.png':
            mimetype = 'image/png'
        else:
            print(f"❌ نوع الصورة غير مدعوم: {thumbnail_path}")
            return

        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaIoBaseUpload(open(thumbnail_path, 'rb'), mimetype=mimetype)
        ).execute()
        print(f"✅ تم رفع Thumbnail: {thumbnail_path}")
    except Exception as e:
        print(f"❌ فشل رفع Thumbnail: {e}")

# ----------------- رفع الفيديوهات -----------------
def generate_title():
    keyword = random.choice(TRENDING_KEYWORDS)
    template = random.choice(TITLE_TEMPLATES)
    return template.format(keyword=keyword), keyword

def generate_description(keyword):
    template = random.choice(DESCRIPTION_TEMPLATES)
    return template.format(keyword=keyword)

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
    media = MediaIoBaseUpload(open(file_path, 'rb'), mimetype="video/*", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = request.execute()
    video_id = response['id']
    print(f"✅ تم النشر: https://youtu.be/{video_id}")

    # رفع Thumbnail تلقائي
    thumbnail_path = get_random_thumbnail()
    if thumbnail_path:
        set_thumbnail(youtube, video_id, thumbnail_path)

def publish_youtube_short(youtube, drive, file):
    tmp_path = download_video(drive, file)
    try:
        title, keyword = generate_title()
        description = generate_description(keyword)
        upload_video_to_youtube(youtube, tmp_path, title, description, HASHTAGS)
        save_posted(file['name'])
        time.sleep(10)
    except Exception as e:
        print(f"❌ فشل النشر: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

# ----------------- المهمة المجدولة -----------------
def main():
    print("🔐 تسجيل الدخول إلى YouTube و Google Drive...")
    youtube = get_youtube_service()
    drive = get_drive_service()
    posted = load_posted()

    def job():
        all_files = get_videos_from_drive(drive)
        available = [f for f in all_files if f['name'].endswith('.mp4') and f['name'] not in posted]
        if not available:
            print("🚫 لا توجد فيديوهات جديدة")
            return
        random.shuffle(available)
        publish_youtube_short(youtube, drive, available[0])

    # نشر الفيديو مرتين يوميًا
    schedule.every().day.at("12:00").do(job)
    schedule.every().day.at("19:00").do(job)

    print("⏰ السكربت يعمل تلقائيًا...")
    try:
        while True:
            schedule.run_pending()
            time.sleep(60)
    except KeyboardInterrupt:
        print("🛑 تم إيقاف السكربت.")

if __name__ == "__main__":
    main()
