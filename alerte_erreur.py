import re
import time
import asyncio
import requests
from telethon import TelegramClient, events

# ====== TES INFOS ======
API_ID = 26321237
API_HASH = "0c190280234e0f9a34b1c9943d060ba4"

# ⚠️ Token bot (évite de le repartager)
BOT_TOKEN = "8122330719:AAFHMd7kPpyA_yXmdHgM9D_0M2vspjykkbk"

# Groupes
NEXEN_CHAT_ID = -1002197482751
VALUE_CHAT_ID = -1001174759265

# Groupe privé (toi + ton ami) où envoyer les alertes
ALERT_GROUP_ID = -5166855510
# =======================

# Patterns
PATTERN_ERREUR = re.compile(r"\berreurs?\b", re.IGNORECASE)
PATTERN_VALUE  = re.compile(r"\bvalues?\b", re.IGNORECASE)

# Latence / anti-spam
COOLDOWN_SECONDS = 2  # mets 0 si tu veux tout, instant (attention spam)

client = TelegramClient("session_erreur", API_ID, API_HASH)

last_sent = 0.0
lock = asyncio.Lock()


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
    payload = {
        "chat_id": ALERT_GROUP_ID,
        "text": text,
        "disable_web_page_preview": True
    }
    r = requests.post(url, json=payload, timeout=10)
    r.raise_for_status()


@client.on(events.NewMessage)
async def handler(event):
    global last_sent

    chat_id = event.chat_id
    if chat_id not in (NEXEN_CHAT_ID, VALUE_CHAT_ID):
        return

    text = event.raw_text or ""

    # Règles par groupe :
    # - Nexen: seulement erreur/erreurs
    # - Autre: erreur/erreurs + value/values
    if chat_id == NEXEN_CHAT_ID:
        if not PATTERN_ERREUR.search(text):
            return
        found = "erreur/erreurs"
    else:
        found_list = []
        if PATTERN_ERREUR.search(text):
            found_list.append("erreur/erreurs")
        if PATTERN_VALUE.search(text):
            found_list.append("value/values")
        if not found_list:
            return
        found = " + ".join(found_list)

    # anti-spam léger
    now = time.time()
    async with lock:
        if COOLDOWN_SECONDS and (now - last_sent) < COOLDOWN_SECONDS:
            return
        last_sent = now

    chat_title = getattr(event.chat, "title", None) or str(chat_id)
    topic_id = get_topic_id(event.message)
    link = tme_link(chat_id, event.message.id, topic_id)

    msg = (
        f"⚠️ Mot détecté : {found}\n\n"
        f"• Groupe: {chat_title}"
        + (f" | topic_id={topic_id}" if topic_id else "")
        + "\n"
        f"• Message:\n{clip(text)}\n\n"
        + (f"• Lien: {link}" if link else "")
    )

    try:
        await asyncio.to_thread(bot_send_text, msg)
    except Exception as e:
        print("❌ sendMessage error:", repr(e))


async def main():
    await client.start()
    print("✅ Actif — Nexen: erreur/erreurs | Autre: erreur/erreurs + value/values")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
