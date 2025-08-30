import os
import random
import time
import tempfile
import json
import csv
import subprocess
from datetime import datetime
import schedule
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

"""
YouTube Shorts Publisher — Research‑Driven version (2025)

ما الجديد ولماذا:
1) فحص الفيديو قبل الرفع: نتحقق من الطول (الأفضل 20–45ث، مسموح لحد 60ث)، والاتجاه 9:16، والدقة ≥720p. إن لم يطابق، نرفض أو نصلّح تلقائيًا (اختياريًا بقص الطول).
2) عناوين/وصف ديناميكيان: توليد يعتمد كلمات مفتاحية + Hook + سنة/سياق، وهاشتاغات قليلة وذات صلة (3–5 فقط).
3) جدول نشر ثابت (مرتين يوميًا) + نافذة عشوائية ±15 دقيقة لتفادي نمط روبوتي.
4) حفظ سجل CSV بكل عملية (id، الرابط، العنوان، الوقت، الtags).
5) إمكانية تعليق أول تلقائي (اختياري) لرفع التفاعل المبكر.
6) إعادة المحاولة والتنظيف والـ logging.

ملاحظات تشغيل:
- يتطلب FFmpeg (ffprobe) متاحًا في PATH لقراءة خصائص الفيديو. إن لم يتوفر، سيتخطى الفحص ويطبع تحذيرًا.
- TOKEN_JSON (OAuth user) و SERVICE_ACCOUNT_JSON (Drive) يجب تمريرهما كمتغيرات بيئية.
"""

# ====================== إعدادات Google API ======================
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",  # للتعليقات الاختيارية
    "https://www.googleapis.com/auth/drive.readonly",
]
POSTED_LOG = "posted_from_drive.txt"
CSV_LOG = "uploads_log.csv"

# ====================== معلمات الخوارزمية ======================
# أفضل أداء عادة 20-45 ثانية. الحد الأقصى المنصوح به 60 ثانية لShorts.
MIN_RES_HEIGHT = 720
TARGET_MIN_SEC = 20
TARGET_MAX_SEC = 45
HARD_MAX_SEC = 60
ASPECT_MIN = 0.55   # ~9:16 = 0.5625
ASPECT_MAX = 0.6

# كلمات مفتاحية و Hooks
KEYWORDS = ["نجاح", "تحفيز", "ريادة أعمال", "مال", "أسرار النجاح", "تطوير الذات", "انضباط", "عادات"]
HOOKS = [
    "🚀 سر لا يعرفه الكثير:",
    "❌ أكبر خطأ يمنعك من:",
    "🔥 السر وراء",
    "💡 كيف تبدأ بـ",
    "🧠 عقلية:",
    "⏳ دقيقة تغيّر نظرتك عن",
]
CTA_ENDINGS = [
    "اكتب \"جاهز\" لو ناوي تبدأ اليوم!",
    "اختر 1 أو 2 وقل لي لماذا في التعليقات.",
    "اشترك لو أعجبك المحتوى." ,
]

# هاشتاغات: قليلة ومركزة (3–5)
HASHTAG_POOL = [
    "#Shorts", "#نجاح", "#تحفيز", "#تطوير_الذات", "#ريادة_أعمال", "#عقلية_غنية", "#ملهم", "#اهداف"
]

# أوقات النشر — ثابته مع انحراف صغير عشوائي
PUBLISH_TIMES = ["8:00", "15:00"]
RANDOM_WINDOW_MINUTES = 15

# ====================== أدوات مساعدة ======================

def _run_ffprobe(path):
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration",
            "-of", "json",
            path,
        ]
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        info = json.loads(out.decode("utf-8"))
        stream = info["streams"][0]
        width = int(stream.get("width", 0))
        height = int(stream.get("height", 0))
        duration = float(stream.get("duration", 0.0))
        return width, height, duration
    except Exception as e:
        print(f"⚠️ ffprobe غير متاح أو فشل التحليل: {e}")
        return None

def validate_video_props(path):
    meta = _run_ffprobe(path)
    if not meta:
        # لا نفشل النشر لكن نحذر
        return True, {"note": "no-ffprobe"}
    w, h, dur = meta
    aspect = h and w / h or 0
    issues = []
    if h < MIN_RES_HEIGHT:
        issues.append(f"الدقة منخفضة: {h}px < {MIN_RES_HEIGHT}px")
    if not (ASPECT_MIN <= aspect <= ASPECT_MAX):
        issues.append(f"الاتجاه غير رأسي 9:16 (aspect={aspect:.3f})")
    if dur > HARD_MAX_SEC:
        issues.append(f"الطول {dur:.1f}s > {HARD_MAX_SEC}s")
    ok = len(issues) == 0
    return ok, {"width": w, "height": h, "duration": dur, "aspect": aspect, "issues": issues}

def maybe_trim_to_target(path):
    """اختياري: قص الفيديو إلى HARD_MAX_SEC إذا كان أطول — يتطلب ffmpeg."""
    meta = _run_ffprobe(path)
    if not meta:
        return path
    _, _, dur = meta
    if dur <= HARD_MAX_SEC:
        return path
    out_path = path.replace(".mp4", "_trim.mp4")
    try:
        subprocess.check_call(["ffmpeg", "-y", "-i", path, "-t", str(HARD_MAX_SEC), "-c", "copy", out_path])
        return out_path
    except Exception as e:
        print(f"⚠️ فشل القص: {e}")
        return path

# ====================== توليد العنوان/الوصف ======================

def extract_keyword_from_filename(name: str):
    base = os.path.splitext(name)[0]
    tokens = [t for t in base.replace("_", " ").replace("-", " ").split() if len(t) >= 3]
    for t in tokens:
        if any(k in t for k in ["نجاح", "تحفيز", "مال", "ريادة", "discipline", "success", "motivation"]):
            return t
    return None

def choose_hashtags(n=4):
    n = max(3, min(n, 5))
    return random.sample(HASHTAG_POOL, n)

def generate_title(filename: str):
    hook = random.choice(HOOKS)
    kw = extract_keyword_from_filename(filename) or random.choice(KEYWORDS)
    year_note = " 2025"
    return f"{hook} {kw}{year_note} #Shorts"

def generate_description():
    kw = random.choice(KEYWORDS)
    cta = random.choice(CTA_ENDINGS)
    tags = " ".join(choose_hashtags())
    return (
        f"💡 {kw} في أقل من دقيقة.\n"
        f"🚀 هذا الفيديو يريك كيف تبدأ من الصفر وتطور نتائجك بسرعة.\n\n"
        f"✅ {cta}\n\n"
        f"{tags}"
    )

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

# CSV Log header
if not os.path.exists(CSV_LOG):
    with open(CSV_LOG, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "drive_name", "youtube_id", "youtube_url", "title", "hashtags", "notes"])  # header


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

# ====================== رفع الفيديو/التعليق ======================

def upload_video_to_youtube(youtube, file_path, title, description, tags=None):
    tags = tags or []
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": [t.strip("#") for t in tags],
            "categoryId": "22"
        },
        "status": {"privacyStatus": "public"}
    }
    media = MediaIoBaseUpload(open(file_path, "rb"), mimetype="video/*", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = request.execute()
    vid = response["id"]
    print(f"✅ تم النشر على يوتيوب: https://youtu.be/{vid}")
    return vid


def post_first_comment(youtube, video_id: str, text: str):
    try:
        body = {
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {"snippet": {"textOriginal": text}}
            }
        }
        youtube.commentThreads().insert(part="snippet", body=body).execute()
        print("💬 تم نشر أول تعليق.")
    except Exception as e:
        print(f"⚠️ تعذر نشر التعليق: {e}")


# ====================== مهمة نشر فيديو ======================

def publish_youtube_short(youtube, drive, file):
    tmp_path = download_video(drive, file)
    local_path = tmp_path
    notes = []
    try:
        # فحص الفيديو
        ok, meta = validate_video_props(local_path)
        if not ok:
            print("🚫 سيتم تخطي الفيديو بسبب مشكلات:", meta.get("issues"))
            notes.append(";".join(meta.get("issues", [])))
            return None
        # قص قسري لو أطول من HARD_MAX_SEC
        local_path = maybe_trim_to_target(local_path)

        title = generate_title(file["name"])  
        description = generate_description()
        hashtags = choose_hashtags()
        vid = upload_video_to_youtube(youtube, local_path, title, description, hashtags)

        # تعليق أول اختياري لتعزيز التفاعل المبكر
        post_first_comment(youtube, vid, "اكتب \"جاهز\" لو ناوي تبدأ اليوم! 🚀")

        save_posted(file["name"])  # سجل النص

        url = f"https://youtu.be/{vid}"
        with open(CSV_LOG, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.now().isoformat(timespec="seconds"),
                file["name"], vid, url, title, " ".join(hashtags),
                json.dumps(meta, ensure_ascii=False) if meta else ""
            ])
        return url
    except Exception as e:
        print(f"❌ فشل النشر: {e}")
        return None
    finally:
        try:
            if os.path.exists(local_path) and local_path != tmp_path:
                os.remove(local_path)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

# ====================== الجدولة بزمن عشوائي بسيط ======================

def _times_with_jitter(times, minutes=RANDOM_WINDOW_MINUTES):
    from datetime import timedelta
    rand = random.randint
    out = []
    for t in times:
        hh, mm = map(int, t.split(":"))
        # إضافة إزاحة عشوائية سالبة/موجبة
        delta = rand(-minutes, minutes)
        new_m = (hh * 60 + mm + delta) % (24 * 60)
        nh, nm = divmod(new_m, 60)
        out.append(f"{nh:02d}:{nm:02d}")
    return sorted(set(out))


def main():
    print("🔐 تسجيل الدخول إلى YouTube و Google Drive...")
    youtube = get_youtube_service()
    drive = get_drive_service()
    posted = load_posted()

    def job():
        all_files = get_videos_from_drive(drive)
        available = [f for f in all_files if f["name"].lower().endswith((".mp4", ".mov", ".mkv")) and f["name"] not in posted]
        if not available:
            print("🚫 لا توجد فيديوهات جديدة")
            return
        random.shuffle(available)
        url = publish_youtube_short(youtube, drive, available[0])
        if url:
            print("✅ تم النشر:", url)

    # أوقات نشر مع انحراف عشوائي لمنع النمطية
    for t in _times_with_jitter(PUBLISH_TIMES):
        schedule.every().day.at(t).do(job)
        print("⏰ تم ضبط موعد:", t)

    print("⏰ السكربت يعمل تلقائيًا...")
    try:
        while True:
            schedule.run_pending()
            time.sleep(60)
    except KeyboardInterrupt:
        print("🛑 تم إيقاف السكربت.")


if __name__ == "__main__":
    main()
