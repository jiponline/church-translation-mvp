# Church Translation — Phase A

เซิร์ฟเวอร์ Python (stdlib ล้วน, asyncio) — ถอดเสียงยังใช้ Chrome ที่ gateway, การแปลทำที่เซิร์ฟเวอร์

## รัน

```bash
PUBLISH_TOKEN=รหัสลับของคุณ python3 server.py
```

ถ้าไม่ตั้ง `PUBLISH_TOKEN` เซิร์ฟเวอร์จะสุ่มให้และพิมพ์ใน log

- ผู้ฟัง: `/` (เปิดได้ทุกคน) · Gateway: `/gateway` (ต้องกรอก token) · แอดมิน: `/admin`
- `GOOGLE_TRANSLATE_API_KEY` — ถ้าตั้งไว้ จะใช้ Google Cloud Translation อัตโนมัติ (ส่ง key ทาง header ไม่ใส่ใน URL/log)
- `TRANSLATION_PROVIDER=google | mymemory | echo` — บังคับเลือกเอง (ไม่มี key จะใช้ `mymemory` ซึ่งฟรีแต่โควตาหมดเร็ว ใช้ทดสอบเท่านั้น)
- `MAX_LISTENERS` (ค่าเริ่มต้น 2000)

## ทดสอบ

```bash
python3 -I -m unittest discover -s tests
```

## API

- `GET /events?lang=th|en|zh|ko|ja` — SSE รายภาษา มี `id` และรองรับ `Last-Event-ID`
- `POST /api/segment` (Bearer token) — `{source_text, source_lang, targets[]}` → 202, แปลแบบขนาน
- `POST /api/gateway/heartbeat` (Bearer token), `GET /api/status`, `GET /health`

## ยังไม่ได้ทำ

ASR ฝั่งเซิร์ฟเวอร์ + ตรวจภาษาอัตโนมัติ, TTS, ฐานข้อมูล/หลาย tenant, ทดสอบโหลด 500 ผู้ฟัง
`server.js` / `package.json` เป็นโค้ดเดโมรุ่นเก่าที่ไม่ได้ใช้แล้ว
