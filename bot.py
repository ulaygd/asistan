import os, asyncio, random, datetime as dt, logging
from zoneinfo import ZoneInfo
from google import genai
from google.genai import types
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
TZ = ZoneInfo("Europe/Istanbul")
ID_FILE = "chat_id.txt"

SYSTEM = """Sen Jarvis'sin: kullanıcının kişisel asistanı ve açık sözlü dostu. Türkçe konuş.
Kurallar:
- Kullanıcıya hep hak verme. Mantık hatası, kötü karar veya boş umut görürsen nazik ama net söyle, gerekçeni göster.
- Ara sıra onu geliştirecek tek bir düşündürücü soru sor (her mesajda değil).
- Konuyu mesajdan anla: ekonomi/fon, siyaset, YKS, futbol, kod/proje, genel sohbet.
- Güncel bilgi gerekiyorsa web aramasını kullan, uydurma. Emin değilsen "emin değilim" de.
- Siyaset: tarafsız anlat, farklı görüşlerin en güçlü savunularını ver, kendi oyunu/tarafını söyleme.
- Ekonomi/fon: eğitim amaçlı analiz yap, kesin al-sat emri verme, riski mutlaka söyle.
- Futbol kuponu: olasılık, gerekçe ve riski göster. Kazanç garantisi yok. Gün uygun değilse "bugün kupon yapma" de. Kaybetmeyi göze alabileceğinden fazlasını yatırmasın.
- YKS: konu anlat, soru çöz, hatayı göster, kısa tekrar soruları sor.
- Kısa yaz: telefon ekranında rahat okunsun."""

SLOTS = {
    (8, 0): "Sabah brifingi ver. Web aramasıyla güncel bilgi al, her maddeyi 2-3 cümlede tut: (1) Türk ekonomisi, borsa, döviz ve fonlarda önemli gelişmeler; varsa piyasa/fon krizi uyarısı. (2) Dünyadaki savaşlar ve çatışmaların güncel durumu. (3) Doğu Türkistan dahil dünya siyasetindeki önemli gelişmeler, tarafsız anlat. (4) Dikkat çekebilecek 2-3 fon veya hisse fikri: gerekçesi ve riskiyle ver, sonuna kısa bir not düş: yatırım tavsiyesi değil, kendi araştırmamla karar vermeliyim. Sonunda beni düşündürecek tek bir soru sor.",
    (13, 0): "Bana YKS için kısa bir ders çalışma dürtmesi yap: bir konu seç, 3 cümlede anlat, sonra 1 soru sor. Cevabımı bekle.",
    (10, 0): "Bugünün ve yarının maçlarına bak. Yüksek olasılıklı 2-3 maçı gerekçe ve riskle öner. Uygun maç yoksa kupon yapma de. Kazanç garantisi olmadığını kısaca belirt.",
}

EXPLORE = (
    "Ufkumu açacak bir şey öner: ekonomi, siyaset, tarih, bilim veya teknolojiden beklenmedik bir konu seç. "
    "Bana 1 araştırma fikri ya da düşündürücü bir soru ver; neden ilginç olduğunu ve nereden başlamam gerektiğini 2-3 cümleyle anlat. "
    "Doğru bilgi için web aramasını kullan. Daha önce önerdiklerinden farklı bir alan seç."
)

history: list[types.Content] = []


def get_owner():
    if os.getenv("OWNER_CHAT_ID"):
        return int(os.environ["OWNER_CHAT_ID"])
    if os.path.exists(ID_FILE):
        return int(open(ID_FILE).read().strip())
    return None


async def ask(parts, note):
    contents = history[-20:] + [types.Content(role="user", parts=parts)]
    r = await client.aio.models.generate_content(
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )
    text = r.text or "Cevap üretemedim, tekrar dener misin?"
    history.append(types.Content(role="user", parts=[types.Part.from_text(text=note)]))
    history.append(types.Content(role="model", parts=[types.Part.from_text(text=text)]))
    del history[:-40]
    return text


async def send(bot, chat_id, text):
    for i in range(0, len(text), 4000):
        await bot.send_message(chat_id, text[i:i + 4000])


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    owner = get_owner()
    if owner and owner != update.effective_chat.id:
        return
    open(ID_FILE, "w").write(str(update.effective_chat.id))
    await update.message.reply_text(
        f"Jarvis çevrimiçi. Chat ID'n: {update.effective_chat.id}\n"
        "Yaz ya da sesli mesaj at. Sabah, öğlen ve akşam kendim de yazacağım."
    )


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    owner = get_owner()
    if not owner or update.effective_chat.id != owner:
        return  # önce /start gerekli; başkalarını yok sayar
    m = update.message
    try:
        if m.voice:
            f = await m.voice.get_file()
            data = bytes(await f.download_as_bytearray())
            parts = [
                types.Part.from_bytes(data=data, mime_type="audio/ogg"),
                types.Part.from_text(text="(Sesli mesaj. Ne dediğimi anla ve cevap ver.)"),
            ]
            note = "[sesli mesaj]"
        elif m.text:
            parts = [types.Part.from_text(text=m.text)]
            note = m.text
        else:
            return
        await ctx.bot.send_chat_action(owner, "typing")
        await send(ctx.bot, owner, await ask(parts, note))
    except Exception as e:
        logging.exception(e)
        await m.reply_text("Bir hata oldu, birazdan tekrar dene.")


async def push(ctx: ContextTypes.DEFAULT_TYPE):
    owner = get_owner()
    if not owner:
        return
    prompt = ctx.job.data
    try:
        text = await ask([types.Part.from_text(text=prompt)], prompt)
        await send(ctx.bot, owner, text)
    except Exception as e:
        logging.exception(e)


async def push_random(ctx: ContextTypes.DEFAULT_TYPE):
    # Ufuk açıcı mesaj her gün gelmez; yaklaşık iki günde bir
    if random.random() < 0.5:
        await push(ctx)


def main():
    app = Application.builder().token(TG_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler((filters.TEXT & ~filters.COMMAND) | filters.VOICE, on_message))
    for (h, mi), prompt in SLOTS.items():
        app.job_queue.run_daily(push, dt.time(h, mi, tzinfo=TZ), data=prompt, name=f"push-{h}-{mi}")
    app.job_queue.run_daily(push_random, dt.time(17, 0, tzinfo=TZ), data=EXPLORE, name="explore")
    app.run_polling()


if __name__ == "__main__":
    main()
  
