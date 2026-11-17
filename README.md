# Church Translation System — MVP 0.1

นี่คือ vertical slice แรกที่รันได้จริงในเครื่อง: เว็บผู้ฟังเชื่อมต่อ WebSocket และรับข้อความแปลสดแบบจำลองจากเซิร์ฟเวอร์ทุก 5 วินาที

## เริ่มใช้งานแบบไม่ต้องติดตั้งโปรแกรมเพิ่ม

```bash
python3 server.py
```

เปิด `http://127.0.0.1:8000` แล้วกด **เชื่อมต่อระบบ**

ตรวจสุขภาพระบบได้ที่ `http://localhost:3000/health`

## สิ่งที่ทำแล้ว

- HTTP server สำหรับหน้าเว็บผู้ฟัง
- Real-time event endpoint ที่ `/events`
- session จำลองหนึ่ง session
- event แบบ `session.status`, `subscribed`, และ `text.segment`
- เลือกภาษาผู้ฟัง: อังกฤษ จีน เกาหลี ญี่ปุ่น ไทย

## สิ่งที่ต้องต่อในขั้นถัดไป

- รับเสียงจริงจากมิกเซอร์/encoder
- เชื่อม ASR เพื่อถอดเสียงและตรวจจับภาษาอัตโนมัติ
- เชื่อม Translation และ TTS provider
- เพิ่ม PostgreSQL, ผู้ใช้, คริสตจักรหลาย tenant และ authentication
- ส่ง audio segments ให้ผู้ฟังเล่นจริง

โค้ดนี้เป็น MVP สำหรับตรวจสอบการไหลของระบบ ไม่ใช่ระบบ production และยังไม่ได้เชื่อม AI หรือเสียงจริง
