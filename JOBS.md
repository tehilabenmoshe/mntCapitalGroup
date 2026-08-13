# 🚀 סוכן חיפוש משרות הייטק — Junior Full-Stack / Frontend / AI

סוכן שרץ **פעם ביום בענן** (GitHub Actions), מוצא משרות **חדשות** שמתאימות
לפרופיל שלך (Junior/Entry ב-Full-Stack / Frontend / AI), מדרג אותן לפי התאמה,
ושולח דייג'סט למייל **bmtehila@gmail.com** — עם לינק ישיר להגשה לכל משרה.

> ⚠️ הסוכן **לא מגיש** קורות חיים לבד. הוא מוצא, מסנן ומדרג — ואת מגישה בעצמך
> את מה שמעניין אותך. זה שומר על איכות ההגשות ולא מסכן חשבונות/מוניטין.

## איך זה עובד
- `scripts/check_jobs.py` מושך משרות משני סוגי מקורות:
  1. **לוחות ATS רשמיים של חברות ישראליות** (Greenhouse/Lever) — מוגדרות ב-
     `config/companies.json` (21 חברות מאומתות). JSON ציבורי וחוקי, בלי סקרייפינג.
  2. **אגרגטורים חינמיים של משרות Remote** (Remotive, Arbeitnow) שמקבלים
     מועמדים מישראל/עולמי.
- מסנן ל-Junior/Entry בלבד בתחומי FS/FE/AI, במיקום ישראל או Remote.
- מדרג כל משרה ב**ציון התאמה** מול הסטאק שלך (React, Node, TS, C#/.NET, SQL,
  AI/RAG…), ומצרף שורת "למה זה מתאים".
- **מצרף את קובץ קורות החיים** (`assets/Tehila_Michaeli_CV.pdf`) לכל מייל.
- **טיוטת Cover Letter מותאמת לכל משרה** מובילה — נכתבת ע"י Claude אם מוגדר
  `ANTHROPIC_API_KEY` (אחרת תבנית קבועה עם שם החברה/המשרה).
- משווה מול `state/previous_job_ids.json` (מה שכבר נשלח) ושולח **רק משרות חדשות**.
- `.github/workflows/daily-jobs.yml` מריץ כל בוקר ~09:00 שעון ישראל ומחזיר
  לריפו את קובץ ה-state המעודכן.

**עלות: 0 ₪.** כל המקורות חינמיים וללא מכסת בקשות — אין צורך במונה/תקרה
כמו בסוכן הבתים.

## הגדרה חד-פעמית

### שלב 1 — Gmail App Password (לשליחת המייל)
1. הפעילי אימות דו-שלבי בחשבון ה-Gmail השולח.
2. https://myaccount.google.com/apppasswords → צרי App Password ("Mail") →
   שמרי את ה-16 תווים.

### שלב 2 — Secrets ברפוזיטורי
ב-GitHub: **Settings → Secrets and variables → Actions → Secrets**

| שם | ערך |
|---|---|
| `GMAIL_USER` | כתובת ה-Gmail השולחת (למשל `bmtehila@gmail.com`) |
| `GMAIL_APP_PASSWORD` | ה-App Password (16 תווים) |

> כתובת היעד היא `bmtehila@gmail.com` כברירת מחדל — לא צריך להגדיר כלום.
> אם תרצי לשלוח למייל אחר, הוסיפי Secret בשם `JOBS_EMAIL_TO`.

### שלב 3 (רשות) — Variables לכוונון עדין
**Settings → Secrets and variables → Actions → Variables** (לא סודי, קל לערוך):

| שם | ברירת מחדל | משמעות |
|---|---|---|
| `INCLUDE_ISRAEL` | `true` | לכלול משרות בחברות ישראליות |
| `INCLUDE_REMOTE` | `true` | לכלול משרות Remote |
| `MIN_SCORE` | `2` | סף ציון מינימלי (גבוה יותר = פחות משרות, אבל מדויקות יותר) |
| `MAX_EMAIL_JOBS` | `40` | מקסימום משרות במייל אחד |
| `COVER_LETTER_MAX` | `10` | לכמה משרות מובילות לכתוב Cover Letter |
| `COVER_LETTER_MODEL` | `claude-haiku-4-5` | מודל Claude ל-Cover Letters (Haiku = זול) |

### שלב 3.5 (רשות) — Cover Letters מותאמים ע"י Claude
כדי ש-Claude יכתוב טיוטת Cover Letter מותאמת לכל משרה מובילה, הוסיפי Secret
בשם `ANTHROPIC_API_KEY` (מ-https://console.anthropic.com). בלי המפתח — תישלח
תבנית קבועה עם שם החברה/המשרה (עדיין שימושי, וללא עלות). העלות עם Haiku זניחה
(~סנט ליום ל-10 משרות).

### שלב 4 — בדיקה ידנית
טאב **Actions** → "Daily job finder" → **Run workflow**. ודאי שהמייל מגיע ושאין
שגיאות בלוג. משם זה רץ אוטומטית כל בוקר.

## הוספת/הסרת חברות
פשוט ערכי את `config/companies.json`. כדי למצוא טוקן של חברה: אם דף הקריירה שלה
הוא `https://boards.greenhouse.io/COMPANY` — הטוקן הוא `COMPANY`. הוסיפי אותו
לרשימת `greenhouse` (או `lever` בהתאמה). כל הטוקנים הקיימים אומתו כעובדים.

רעיונות להרחבה: הוסיפי חברות שמעניינות אותך (Wiz, Gong, Monday וכו' — לרבות
משתמשות ב-Comeet/Ashby שנוכל להוסיף תמיכה בהם בהמשך).

## בדיקה מקומית (בלי לשלוח מייל)
```bash
GMAIL_USER=x GMAIL_APP_PASSWORD=x python scripts/check_jobs.py --dry-run
```
מדפיס את המשרות המתאימות בלי לשלוח מייל ובלי לעדכן state.

## פרטיות
קובץ `assets/Tehila_Michaeli_CV.pdf` מכיל פרטים אישיים (טלפון, מייל). **הריפו
חייב להישאר פרטי** (הוא כבר פרטי). אל תהפכי אותו ל-Public.

## שדרוגים אפשריים (בהמשך)
- תמיכה ב-Comeet / Ashby / Workday לחברות נוספות.
- שליחה ל-Telegram/WhatsApp במקום/בנוסף למייל.
- הפרדת הסוכן לריפו נפרד משלו (כרגע הוא חי לצד סוכן הבתים).
