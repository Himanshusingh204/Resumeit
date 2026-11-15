# Resumeit: Plan

Single-user, local tool. Jobs scrape karta hai (JobSpy), har job ke liye tumhare resume se ek tailored resume banata hai.
**Apply hamesha tum karte ho.** App sirf taiyari karta hai.

---

## 1. Non-negotiable rules

Ye rules har feature pe lagte hain. Koi feature inhe todta hai to wo nahi banega.

### 1.1 Auto-apply kabhi nahi
- App kisi job pe **apply nahi karta**. Na form bharta hai, na email bhejta hai, na LinkedIn/Naukri "Easy Apply" dabata hai.
- Roz ka auto-scan (jab banega) sirf **jobs dhundhega aur resume banayega**. Kabhi submit nahi karega.
- "Applied" status sirf tum haath se mark karte ho. App khud kabhi "applied" nahi likhta.
- Apply ke liye sirf "Open job" link hai, jo original site khol deta hai.
- Koi loop jo bina tumhare click ke naye kaam shuru kare, sirf *dhundhne* ya *resume banane* ka ho sakta hai, aur wo bhi tumhare set kiye hue rules ke andar (kitni jobs, kitni AI calls). Hamesha "Stop" button ke saath.

### 1.2 Resume me jhooth nahi
Tailored resume me **sirf wahi facts** aayenge jo tumhare base resume (profile) me hain.
- Nayi skill, company, title, date, degree, certificate, ya number (jaise "40% faster") **nahi jodi jayegi**.
- AI ko ye cheezein allowed hain: bullets ko reorder karna, kuch bullets chhodna, shabd badalna taaki JD ki bhasha se mile, jab tumne wo kaam sach me kiya ho.
- Jo requirement tumhare paas nahi hai, wo **gap** ban ke dikhti hai. Chhupai ya bharai nahi jati.
- Sirf prompt pe bharosa nahi. **Code se bhi check** hoga (section 3, item A).

### 1.3 Resume sirf 1 page
- Har PDF **ek A4 page** ka. Do page ka resume ban hi nahi sakta, ya to chhota hoga ya "manual trim chahiye" ke saath flag hoga.
- Chhota karne ka tarika: *content ghatana* (kam bullets, kam projects), kabhi content badhana nahi (section 3, item A).

---

## 2. Abhi kya bana hua hai

| Kya | Status |
|---|---|
| Resume upload (PDF/DOCX/TXT/paste), parse, edit, delete | chal raha hai |
| Resume se keywords, job search (LinkedIn, Indeed, Naukri, Glassdoor, Google) via JobSpy | chal raha hai |
| Job link ya pasted JD se resume | chal raha hai |
| AI: Claude Code CLI (tested), Anthropic API, OpenAI | OpenAI **untested** (key nahi) |
| Har resume ke liye `jobs/<date>/<company>_<title>_<id>/` folder: `jd.md`, `match.txt`, `resume.pdf/.docx/.html` | chal raha hai |
| 3 templates (Classic, Modern, Compact), preview, PDF, DOCX | chal raha hai |
| Search sirf jobs laati hai. Resume tabhi banta hai jab tum Build dabao (ya checkbox tick karo) | chal raha hai |
| Jobs par "tumhari kitni skills mili" (bina AI ke), low-match jobs pe AI call nahi | chal raha hai |
| Queue: Stop queue, single Build queue skip karta hai | chal raha hai |
| Fail/skip hui cheezein Issues me aur `data/issues.log` me | chal raha hai |
| Light/dark/auto theme, graphite design | chal raha hai |
| 66 automatic tests | pass |

### Jo data me dikha (is plan ki wajah)
- 76 PDFs me se **36 do page ki** hain (lagbhag aadhi). Rule 1.3 isliye pehle.
- ~60 jobs me se ~25 "5+ years" maangti hain. App abhi inhe pehchanti nahi.
- Jab queue lambi hoti hai to resume banane me kaafi time aur AI quota lagta hai (naapa nahi gaya).

---

## 3. Roadmap

Order: **A, B, C** ek saath (sabse zaroori), phir D, E, phir F, G, H.

### A. Resume: jhooth nahi + 1 page (rules 1.2 aur 1.3)
**Kyun:** Ye app ka bharosa hai. Galat ya 2 page ka resume bhejna nuksan hai.

**Kaam:**
1. **Truth guard (code se).** Tailored resume banne ke baad check:
   - har skill profile ki skills me hai (case ignore),
   - har company/title/date/degree profile se match karta hai,
   - bullets me jo numbers hain wo profile ke bullets me bhi hain,
   - koi nayi certification/project nahi.
   Koi bhi check fail ho to resume **reject**, job "Failed" dikhe, reason Issues me likha jaye. Chupchap nahi.
2. **1 page guard.** AI ko limit di jaye (har role ke ~3 bullets, ~3 projects, ~12 skills, 2 line summary). PDF banane ke baad page count check:
   - 2 page ho to pehle font/spacing thoda ghatao (9 pt se neeche nahi),
   - phir bhi 2 page ho to sabse kam relevant bullet/project hatao (kabhi naya mat jodo),
   - phir bhi na ho to job pe "manual trim chahiye" flag + Issues entry.
3. Purane 76 PDFs me jo 2 page ke hain unhe **Rebuild** se theek karne ka button (ya "sab 2-page wale dobara").

**Done kab maana jayega:**
- test: ek profile me jhoothi skill/number wala AI jawab aaye to reject hota hai.
- test: lambe profile ka PDF `pypdf` se ginne par **exactly 1 page**.
- DOCX ka page count verify nahi ho sakta. Docs me ye likha jayega.

### B. Applied tracker (haath se)
- Status: `Applied`, `Interview`, `Rejected`, `Offer` (sirf tum badalte ho). Date apne aap lagti hai jab tum mark karo.
- Applied/Rejected jobs default list se hat jayein (tab me "Applied" me dikhein), taaki naye kaam ki list saaf rahe.
- **Auto-apply nahi.** Sirf "Open job" + "Mark applied" button.
- Done: tests me status badalna, filter, aur ye ki app kabhi khud status nahi badalta.

### C. Seniority aur location flag (bina AI ke)
- JD se pakadna: "5+ years", "8-10 years", "US citizenship", "security clearance", "visa sponsorship nahi".
- Tumhare profile ke experience se compare karke card pe chhota nishaan: "5+ yrs chahiye" / "US only".
- Filter option: "mere level se upar wali jobs chhupao".
- Ye sirf nishaan hai. Job delete nahi hoti, tum chaho to phir bhi Build kar sakte ho.
- Done: regex ke tests (alag-alag likhne ke tareeke) aur ek test ki 0 years wali JD pe flag nahi lagta.

### D. "Kya badla" view
- Preview me side-by-side: base resume vs tailored resume, jo hata/badla wo highlight.
- Bhejne se pehle ek nazar me pakad sako ki AI ne kya kiya.
- Done: test ki farak sahi highlight hota hai.

### E. Roz ka auto-scan (apply nahi, sirf taiyari)
- Windows Task Scheduler ek script chalaye: saved search (titles, city, boards) se nayi jobs laye, fit nikale.
- Resume **banane se pehle** limit: max N jobs/roz, sirf wo jinme skills mili, aur 1.1 ke rules.
- Naya kya mila, uska chhota summary (Issues ya ek file `jobs/_daily.md`).
- Done: test ki script kabhi apply/submit wala code nahi chalati, aur limit se zyada resume nahi banata.

### F. Cover letter (optional)
- Har job ke liye chhota cover letter, wohi rule 1.2 (sirf profile ke facts), ek aur AI call.
- Sirf tab banega jab tum chaho. Default band.

### G. AI model aur speed
- `.env` me `CLAUDE_MODEL` (aur Anthropic/OpenAI ke liye bhi) taaki sasta/tez model chun sako.
- Queue me aane se pehle "kitni AI calls lagengi" dikhna (abhi confirm box me hai).

### H. Safai
- `run.bat`: agar app pehle se chal raha hai to naya server na khole, bas browser khol de (pehle do server ek saath mile the).
- `docs/index.html` ko naye behavior ke hisaab se update (auto-build off, queue, fit, folder).
- Ek "Backup" button: `data/resumeit.db` + `jobs/` ka zip.
- OpenAI ka ek live test jab key mile.

---

## 4. Jo nahi banega

- **Auto-apply, auto-submit, auto-message recruiters.** (Rule 1.1)
- Multi-user, login, SaaS, hosting. Ye single-user local tool hai.
- Naya experience/skill "suggest karke" resume me daalna. Gap sirf dikhta hai.
- LinkedIn/Naukri me login karke scrape karna (ban ka risk).

---

## 5. Risks aur dhyan rakhne ki baatein

- **Scraping fragile hai.** LinkedIn/Naukri/Glassdoor jaldi block karte hain. `no_results` normal hai. Proxy abhi nahi lagayi.
- **AI quota.** Har resume ek AI call. 100+ jobs ek saath mat banao. Pehle fit dekh ke chuno.
- **Truth guard perfect nahi.** Wo ye pakadta hai ki *nayi cheez* aayi. Kisi purani cheez ko galat tareeke se *ghuma dena* (jaise "contributed" ko "led") code nahi pakad sakta. Isliye D (Kya badla view) aur bhejne se pehle padhna zaroori hai.
- **Google Fonts** page khulte waqt internet se load hote hain. Offline me system font dikhega.
- **App localhost pe hi chalta hai** (127.0.0.1), bahar se koi nahi khol sakta.

---

## 6. Kaam ka order (checklist)

- [ ] A. Truth guard + 1 page guard (+ purane 2-page wale rebuild)
- [ ] B. Applied tracker
- [ ] C. Seniority/location flag
- [ ] D. "Kya badla" view
- [ ] E. Roz ka auto-scan (apply nahi)
- [ ] F. Cover letter (optional)
- [ ] G. `CLAUDE_MODEL` option
- [ ] H. `run.bat` guard, docs update, backup, OpenAI test
