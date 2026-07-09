# התראות שעתיות על בתים למכירה — Carmel, IN מתחת ל-$300,000

סוכן שרץ כל שעה בענן (GitHub Actions), בודק נכסים למכירה חדשים ב-Carmel, IN
מתחת ל-$300,000 דרך RentCast API, ושולח מייל רק כשיש נכסים **חדשים** שלא
נראו בהרצה הקודמת.

## איך זה עובד
- `scripts/check_listings.py` שולף listings פעילים מ-RentCast, מסנן לפי מחיר,
  משווה מול `state/previous_ids.json` (מה שראינו בהרצה הקודמת), ושולח מייל
  רק על ה-IDs החדשים.
- `.github/workflows/hourly-check.yml` מריץ את הסקריפט כל שעה בשעה עגולה,
  ומחזיר לרפוזיטורי את קובץ ה-state המעודכן (כדי שההרצה הבאה תדע מה כבר נראה).

## שלב 1 — RentCast (מקור הנתונים)
1. הירשם בחינם ב-https://www.rentcast.io/api וקבל API key.
2. תוכנית החינם (50 קריאות/חודש) לא מספיקה לבדיקה שעתית (~744/חודש).
   שדרג לתוכנית **Foundation** ($74/חודש, 1,000 קריאות) כדי לתמוך בבדיקה
   שעתית באופן שוטף.
3. שמור את ה-API key בצד — נצטרך אותו כ-Secret ב-GitHub.

## שלב 2 — Gmail App Password
כדי לשלוח מיילים מ-mntcapitalgroup@gmail.com צריך App Password (לא הסיסמה הרגילה):
1. הפעל אימות דו-שלבי בחשבון ה-Gmail השולח (אם עוד לא מופעל).
2. גלוש ל- https://myaccount.google.com/apppasswords וצור App Password חדש
   (בחר "Mail" / "Other").
3. שמור את ה-16 התווים שנוצרים — זה ה-`GMAIL_APP_PASSWORD`.

## שלב 3 — יצירת רפוזיטורי ב-GitHub
1. צור רפוזיטורי חדש (מומלץ **פרטי**, כי הקובץ מכיל לוגיקה עם נתוני חיפוש אישיים).
2. דחוף את הקוד הזה אליו (ראה פקודות בסוף).

## שלב 4 — הגדרת Secrets ו-Variables ברפוזיטורי
ב-GitHub: Settings → Secrets and variables → Actions

**Secrets** (טאב Secrets):
| שם | ערך |
|---|---|
| `RENTCAST_API_KEY` | ה-API key מ-RentCast |
| `GMAIL_USER` | mntcapitalgroup@gmail.com |
| `GMAIL_APP_PASSWORD` | ה-App Password מ-Gmail (16 תווים) |
| `ALERT_EMAIL_TO` | mntcapitalgroup@gmail.com (או כתובת יעד אחרת) |

**Variables** (טאב Variables — לא סודי, קל לערוך):
| שם | ערך |
|---|---|
| `SEARCH_CITY` | Carmel |
| `SEARCH_STATE` | IN |
| `MAX_PRICE` | 300000 |

## שלב 5 — בדיקה ידנית
לפני שסומכים על הריצה השעתית האוטומטית: בטאב **Actions** ברפוזיטורי, בחר
"Hourly Zillow-area listing check" → **Run workflow** כדי להריץ ידנית ולוודא
שהמייל מגיע ושאין שגיאות בלוג.

## הערות חשובות
- **קצב ההתראה בפועל**: GitHub Actions לא מבטיח שהריצה תתחיל בדיוק בשעה
  העגולה — יכול להיות עיכוב של כמה דקות בעומס. זה עדיין הרבה יותר תכוף
  ומהיר מבדיקה ידנית.
- הרפוזיטורי חייב להישאר עם פעילות (הקומיט האוטומטי של state בכל ריצה דואג
  לזה) — GitHub משבית workflows מתוזמנים ברפוזיטורי ללא שום activity 60 יום.
- אם תרצו לשנות עיר/מדינה/סף מחיר — עדכנו רק את ה-Variables, בלי לגעת בקוד.
- אין כאן scraping ישיר של Zillow — הנתונים מגיעים ממקור API לגיטימי (RentCast),
  כך שאין סיכון חסימה/הפרת תנאי שימוש.

## פקודות להעלאה ל-GitHub (אחרי יצירת רפו ריק ב-GitHub)
```
git init
git add .
git commit -m "Initial hourly listing alert agent"
git branch -M main
git remote add origin <URL-של-הרפו-שיצרת>
git push -u origin main
```
