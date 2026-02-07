import os
import re
import time
import asyncio
import requests
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ========= ENV (Railway Variables) =========
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
SESSION_STRING = os.environ["SESSION_STRING"]

ALERT_GROUP_ID = int(os.environ["ALERT_GROUP_ID"])
NEXEN_CHAT_ID = int(os.environ["NEXEN_CHAT_ID"])
VALUE_CHAT_ID = int(os.environ["VALUE_CHAT_ID"])
# ==========================================

PATTERN_ERREUR = re.compile(r"\berreurs?\b", re.IGNORECASE)
PATTERN_VALUE = re.compile(r"\bvalues?\b", re.IGNORECASE)

COOLDOWN_SECONDS = 2  # 0 si tu veux tout instant (risque spam)
last_sent = 0.0
lock = asyncio.Lock()

# Client = TON COMPTE (StringSession) => lit les groupes sans y mettre un bot
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)


# ===== Dummy HTTP server pour Railway (Web Service) =====
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    # évite de spammer les logs http
    def log_message(self, format, *args):
        return


def run_dummy_server():
    try:
        port = int(os.environ.get("PORT", "8080"))
        server = HTTPServer(("0.0.0.0", port), Handler)
        print(f"🌍 Dummy HTTP server listening on 0.0.0.0:{port}")
        server.serve_forever()
    except Exception as e:
        # Si ça plante, Railway risque de stop => on log au moins
        print("❌ Dummy server crashed:", repr(e))


def clip(s: str, n: int = 500) -> str:
    s = (s or "").strip()
    return (s[:n] + "…") if len(s) > n else s


def get_topic_id(msg):
    rt = getattr(msg, "reply_to", None)
    if rt:
        return getattr(rt, "reply_to_top_id", None)
    return None


def tme_link(chat_id: int, msg_id: int, topic_id):
    sid = str(chat_id)
    if sid.startswith("-100"):
        internal = sid[4:]
        base = f"https://t.me/c/{internal}/{msg_id}"
        if topic_id:
            return base + f"?thread={topic_id}"
        return base
    return None


def bot_send_text(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": ALERT_GROUP_ID, "text": text, "disable_web_page_preview": True}
    r = requests.post(url, json=payload, timeout=20)
    r.raise_for_status()


def bot_send_media(kind: str, file_bytes: bytes, filename: str, caption: str):
    """
    kind: 'photo' | 'video' | 'document'
    """
    if kind == "photo":
        method = "sendPhoto"
        field = "photo"
    elif kind == "video":
        method = "sendVideo"
        field = "video"
    else:
        method = "sendDocument"
        field = "document"

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    data = {"chat_id": str(ALERT_GROUP_ID), "caption": caption}
    files = {field: (filename, file_bytes)}

    r = requests.post(url, data=data, files=files, timeout=60)
    r.raise_for_status()


async def send_alert(event, found: str):
    chat_id = event.chat_id
    chat_title = getattr(event.chat, "title", None) or str(chat_id)
    topic_id = get_topic_id(event.message)
    link = tme_link(chat_id, event.message.id, topic_id)

    base_caption = (
        f"⚠️ Mot détecté : {found}\n"
        f"• Groupe: {chat_title}"
        + (f" | topic_id={topic_id}\n" if topic_id else "\n")
        + f"• Message:\n{clip(event.raw_text or '')}\n"
        + (f"\n• Lien: {link}" if link else "")
    )

    # Si média => download via TON compte puis re-upload via le bot
    if event.message.media:
        try:
            b = await client.download_media(event.message, file=bytes)
            if not b:
                await asyncio.to_thread(bot_send_text, base_caption)
                return

            kind = "document"
            if event.message.photo:
                kind = "photo"
                filename = "image.jpg"
            elif getattr(event.message, "video", None):
                kind = "video"
                filename = "video.mp4"
            else:
                filename = "file.bin"

            await asyncio.to_thread(bot_send_media, kind, b, filename, base_caption)
            return

        except Exception as e:
            print("❌ media relay error:", repr(e))
            await asyncio.to_thread(bot_send_text, base_caption)
            return

    # Texte simple
    await asyncio.to_thread(bot_send_text, base_caption)


@client.on(events.NewMessage)
async def handler(event):
    global last_sent

    chat_id = event.chat_id
    if chat_id not in (NEXEN_CHAT_ID, VALUE_CHAT_ID):
        return

    text = event.raw_text or ""

    # Nexen: erreur/erreurs
    if chat_id == NEXEN_CHAT_ID:
        if not PATTERN_ERREUR.search(text):
            return
        found = "erreur/erreurs"
    # Autre: erreur/erreurs + value/values
    else:
        found_list = []
        if PATTERN_ERREUR.search(text):
            found_list.append("erreur/erreurs")
        if PATTERN_VALUE.search(text):
            found_list.append("value/values")
        if not found_list:
            return
        found = " + ".join(found_list)

    # Anti-spam léger
    now = time.time()
    async with lock:
        if COOLDOWN_SECONDS and (now - last_sent) < COOLDOWN_SECONDS:
            return
        last_sent = now

    await send_alert(event, found)


async def heartbeat():
    # Un petit log rare juste pour prouver que le process vit
    while True:
        await asyncio.sleep(600)  # 10 min
        print("🟢 heartbeat (still alive)")


async def runner():
    # Boucle de reconnexion propre
    while True:
        try:
            await client.run_until_disconnected()
        except Exception as e:
            print("❌ disconnected, retry in 5s:", repr(e))
            await asyncio.sleep(5)
            # Telethon gère bien start/connect ; on retente un start
            try:
                await client.start()
            except Exception as e2:
                print("❌ restart failed, retry in 10s:", repr(e2))
                await asyncio.sleep(10)


async def main():
    # ✅ démarrer le serveur HTTP tout de suite
    threading.Thread(target=run_dummy_server, daemon=True).start()

    # ✅ start = init + auth propre (avec StringSession déjà valide)
    await client.start()
    if not await client.is_user_authorized():
        raise RuntimeError("SESSION_STRING invalide ou expirée. Regénère-la.")

    print("✅ Actif — Nexen: erreur/erreurs | Autre: erreur/erreurs + value/values")

    # Le bot tourne + heartbeat en parallèle
    await asyncio.gather(
        runner(),
        heartbeat(),
    )


if __name__ == "__main__":
    asyncio.run(main())
