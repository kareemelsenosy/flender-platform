<div class="cover-page">
<div class="cover-topbar">
<div class="cover-logo"></div>
<div class="cover-badge">Internal Tool</div>
</div>
<div class="cover-body">
<div class="cover-eyebrow">User Guide &mdash; v1.0</div>
<div class="cover-title">Social Media<br>Content Tracker</div>
<div class="cover-sub">Daily Instagram Monitoring &rarr; Organized Folders &amp; Reports</div>
<div class="cover-divider"></div>
<div class="cover-meta-grid">
<div class="cover-meta-card"><div class="cover-meta-label">Version</div><div class="cover-meta-value">1.0</div></div>
<div class="cover-meta-card"><div class="cover-meta-label">Date</div><div class="cover-meta-value">May 2026</div></div>
<div class="cover-meta-card"><div class="cover-meta-label">Author</div><div class="cover-meta-value">Kareem Elsenosy</div></div>
<div class="cover-meta-card"><div class="cover-meta-label">Company</div><div class="cover-meta-value">FLENDER GROUP</div></div>
</div>
</div>
<div class="cover-bottombar">
<div class="cover-bottombar-left">Confidential &mdash; For Internal Use Only</div>
<div class="cover-bottombar-right">
<span class="cover-dot" style="background:#6366F1;"></span>
<span class="cover-dot" style="background:#8B5CF6;"></span>
<span class="cover-dot" style="background:#EC4899;"></span>
</div>
</div>
</div>

<div class="toc-page">

## Table of Contents

1. What the Tool Does
2. How It Works — Overview
3. The Core Concept: Sessions
4. The Four Rooms (Pages)
   - Dashboard
   - Upload
   - Sessions
   - Records
5. Daily Workflow — Step by Step
   - Step 1: Start a Session
   - Step 2: Do Your Instagram Monitoring
   - Step 3: Upload Each Finding
   - Step 4: Close &amp; Export the Session
6. The Upload Form Explained
   - Customer
   - Brands (Multi-Select)
   - Date
   - Type (Stories / Reels / Posts)
   - Content Type
   - Content Source
7. File Renaming Rules
   - Single Brand
   - Multiple Brands
   - Multiple Files Per Record
8. The Exported ZIP — Folder Structure
9. The Excel File Inside the Export
10. Monthly Summary Report
    - How to Generate
    - What's Inside the Excel
11. Managing Sessions
    - Re-Downloading a Past Session
    - Deleting a Session
12. Searching &amp; Filtering Records
13. Backup &amp; Data Storage
14. Troubleshooting — Common Problems &amp; Fixes
15. Quick Reference Card
16. Appendix A — Field Reference
17. Appendix B — Full Feature List

</div>

# User Guide — Social Media Content Tracker

## 1. What the Tool Does

The Social Media Content Tracker (SMT) is a desktop tool that turns your daily Instagram monitoring routine into an automated pipeline. You drop screenshots from your business partners' Instagram accounts into the tool, tag each one with metadata (who posted it, which brand, what type of post), and at the end of the day or week the tool produces an organized folder ready to drag into Google Drive plus an Excel sheet matching your existing reporting format.

**Before this tool:** You manually renamed every screenshot following the format `Date_Customer_Brand`, created folders for each customer in Google Drive, dragged the right files into the right folders, then opened a Google Sheet and typed one row per upload — Customer, Brand, Number of Posts, Type, Content Type, Content Source, file links, and Date. A typical day took 60–90 minutes of repetitive work.

**After this tool:** You drag screenshots into the app, tag them with 6 dropdowns, and click Upload. The tool renames every file correctly, organizes them into a `Customer/Date/files` folder hierarchy, and writes the spreadsheet row for you. One click at the end of the day produces a ready-to-share ZIP plus Excel file. The same workload now takes 10–15 minutes.

---

**Key capabilities:**

| Capability | Detail |
|---|---|
| Drag &amp; drop upload | Drop image/video files; tag with metadata; submit |
| Multi-brand tagging | One screenshot can be tagged with 1 or many brands at once |
| Automatic file renaming | Files renamed to `Brand_PostType.ext` format |
| Session-based workflow | Group uploads into named batches (e.g. "Week 21") |
| Organized ZIP export | Folder per partner, sub-folder per date, files renamed |
| Excel export per session | Matches your existing Google Sheet column format |
| Monthly summary report | 4-sheet Excel with partner activity, brand activity, totals |
| Permanent searchable history | Every record stays in the database forever |
| Customer &amp; brand memory | Auto-suggests names you've used before to prevent typos |
| 100% local | Runs on your laptop, no cloud, no subscription, no internet required |

---

<div class="page-break"></div>

## 2. How It Works — Overview

```
You open the app in your browser
        ↓
Start a Session and name it (e.g. "Week 21")
        ↓
For each Instagram finding:
   - Drag screenshots into the drop zone
   - Tag Customer + Brands + Date + Type + Content Type + Source
   - Click "Upload & Save"
        ↓
Tool automatically:
   - Renames files to "Brand_PostType.ext"
   - Stores them privately on your laptop
   - Writes a record in the database
        ↓
When done, click "Close & Export Session"
        ↓
ZIP file downloads to your computer:
   Session_{Name}/
      records.xlsx
      {Customer}/{Date}/{renamed files}
        ↓
You drag the folders into Google Drive — done
```

## 3. The Core Concept: Sessions

A **session** is the most important idea in this tool. Think of a session as a labeled folder on your desk that you fill with paperwork over a day or a week, then close and file away.

| Stage | What happens |
|---|---|
| **OPEN** | You start a session and give it a name. All uploads you make now belong to this session. |
| **ACTIVE** | You can upload as many records as you want. The session keeps tracking everything. |
| **CLOSED** | You click "Close &amp; Export Session" — the tool builds your ZIP and locks the session as read-only history. |
| **ARCHIVED** | The session lives in the Sessions room forever. You can re-download its export anytime or browse its records. |

**Rules:**

- Only **one session can be open at a time**. This prevents confusion about where uploads are going.
- You cannot upload anything if no session is open — the tool will prompt you to start one.
- A closed session can still be viewed and re-exported, but you cannot add new uploads to it.
- Closing a session **automatically triggers the export download**.

---

## 4. The Four Rooms (Pages)

The left sidebar has four main rooms you navigate between:

### Dashboard

The home page. Shows:

- The currently active session (or a "Start New Session" card if none is open)
- 4 stat cards: Total Records, Active Customers, Posts This Month, Most Active Brand
- A 30-day upload activity line chart
- A "Top Brands" list
- The **Monthly Summary Report** export card
- A "Recent Uploads" preview table

### Upload

The workshop. Shows:

- A drag-and-drop zone on the left for files
- A metadata form on the right
- A session badge at the top reminding you which session is active
- If no session is open, this page is gated with a friendly prompt to go back to the Dashboard

### Sessions

The archive of all sessions you've ever run. Shows:

- A "Start new session" card at top
- Filter tabs: All / Open / Closed
- A card grid of every session with status, dates, record count, and action buttons
- Click any session card to see its detail page with the records inside and a "Download Export" button

### Records

The firehose view. Shows every single upload record from every session in one big sortable table. You can:

- Filter by Customer name (text input)
- Filter by Brand name (text input)
- Filter by date range
- Filter by session (dropdown)
- Sort by any column
- Delete individual records

---

<div class="page-break"></div>

## 5. Daily Workflow — Step by Step

This is the routine you will follow most days.

### Step 1: Start a Session

1. Open the app in your browser (Dashboard appears)
2. Find the "No active session" card at the top
3. Type a name for the session — anything descriptive works:
   - `Week 21`
   - `Mon-Tue 18-May`
   - `Daily Check 18-05-2026`
4. Click **"Start New Session"**

The card transforms into a glowing blue **ACTIVE SESSION** card showing the session name and statistics.

> **Important:** You can only have one open session at a time. If a session is already open and you want to start a new one, you must close the current one first.

### Step 2: Do Your Instagram Monitoring

This part of the workflow does not change. You:

1. Log into your company Instagram account
2. Visit each business partner's profile
3. When you spot a post featuring one of your brands, screenshot it (or screen-record if it's a video/Reel)
4. Repeat for every partner on your list

Save all screenshots/recordings to a folder on your desktop. They can keep their default names (like `IMG_2391.PNG` or `Screen Recording 2026-05-18.mov`) — the tool will rename them automatically.

### Step 3: Upload Each Finding

For each partner/post you found:

1. Click **"Upload"** in the sidebar
2. Drag the screenshots for this one partner/post into the drop zone (or click to browse)
3. Fill the metadata form on the right:
   - **Customer:** start typing — the dropdown will suggest names you've used before
   - **Brands:** type a brand, press Enter to add as a chip. Add more if the post features multiple brands.
   - **Date:** defaults to today; change if the post was from a different day
   - **Type:** Stories / Reels / Posts
   - **Content Type:** Product IMG / Campaign IMG/VID / Store IMG (w/ brand) / Sales / Other (?)
   - **Content Source:** Brand / Customer / Others (?)
4. Click **"Upload &amp; Save"**

The form clears, ready for the next partner. Each upload takes about 15 seconds.

### Step 4: Close &amp; Export the Session

When you've finished logging all of today's findings:

1. Go back to the **Dashboard**
2. The active session card now shows the totals (e.g. "12 records · 38 files")
3. Click the big blue **"Close &amp; Export Session"** button
4. Confirm the prompt
5. A ZIP file downloads automatically to your Downloads folder

The session is now closed and shows up in the Sessions room as a permanent historical record.

---

<div class="page-break"></div>

## 6. The Upload Form Explained

Each field on the form has a specific role. Here's what they mean and how they're used.

### Customer

The name of the **Business Partner** whose Instagram account you saw the post on. Examples from your data:

- PopUp
- Shift
- Vitruta
- Brux
- Poison Drop
- Sneaker District AD
- Vegnonveg
- Personage

The field is a **searchable combobox**. As you type, the tool suggests names you've used before. You can also type a brand-new partner name — the tool will remember it for next time.

### Brands (Multi-Select)

The brand(s) **featured in the post**. Unlike Customer, this is a **multi-tag picker** — you can add one brand or many. Examples:

- Single brand: `CarharttWIP`
- Two brands: `CarharttWIP`, `Edwin`
- Four brands: `Gramicci`, `Market`, `Twojeys`, `HUF`

How to add a brand: type the name, press **Enter** (or click a suggestion). The brand appears as a removable chip. To remove, click the × on the chip.

This is required — at least one brand must be tagged before you can submit.

### Date

The date the post was uploaded by the business partner. Defaults to today. Format is **DD-MM-YYYY**.

### Type (Post Type)

What kind of Instagram post it is:

| Value | Description |
|---|---|
| **Stories** | A 24-hour Story post |
| **Reels** | A Reel (short video) |
| **Posts** | A regular feed post (single image or carousel) |

This value is used in the file name (see Section 7).

### Content Type

What the content actually shows:

| Value | Description |
|---|---|
| **Product IMG** | A product photo (the brand's product on its own) |
| **Campaign IMG/VID** | A branded campaign image or video |
| **Store IMG (w/ brand)** | A photo from the partner's store showing the brand |
| **Sales** | A sales-focused post (discounts, promotions) |
| **Other (?)** | Anything that doesn't fit the above |

### Content Source

Who created the content:

| Value | Description |
|---|---|
| **Brand** | The brand created the content and the partner shared it |
| **Customer** | The business partner created the content themselves |
| **Others (?)** | Unknown or third-party content |

---

<div class="page-break"></div>

## 7. File Renaming Rules

When you upload files, the tool automatically renames them following a strict format:

```
Brand_PostType.ext
```

The file's original name is replaced. The new name is built from your form input.

### Single Brand

If you tagged only one brand:

| Original name | Brand | Post Type | New name |
|---|---|---|---|
| IMG_2391.PNG | CarharttWIP | Stories | `CarharttWIP_Stories.png` |
| Screen_Rec.mov | Edwin | Reels | `Edwin_Reels.mov` |
| photo.jpg | Gramicci | Posts | `Gramicci_Posts.jpg` |

### Multiple Brands

If you tagged multiple brands, they are joined with underscores in the order you added them:

| Brands tagged | Post Type | New name |
|---|---|---|
| CarharttWIP, Edwin | Stories | `CarharttWIP_Edwin_Stories.png` |
| Gramicci, Market, Twojeys, HUF | Stories | `Gramicci_Market_Twojeys_HUF_Stories.png` |
| Ripndip, The Hundreds | Reels | `Ripndip_TheHundreds_Reels.mp4` |

### Multiple Files Per Record

If you upload several files in a single record (same Customer + Brands + Type), the tool appends a number:

| File count | Filenames |
|---|---|
| 1 file | `CarharttWIP_Stories.png` |
| 3 files | `CarharttWIP_Stories_1.png`, `CarharttWIP_Stories_2.png`, `CarharttWIP_Stories_3.png` |
| 5 files | `CarharttWIP_Stories_1.png` through `CarharttWIP_Stories_5.png` |

This prevents file collisions and keeps every screenshot uniquely named.

> **Note:** The Customer name and Date are **not** in the filename — they live in the folder structure of the exported ZIP instead. This keeps file names short and consistent.

---

<div class="page-break"></div>

## 8. The Exported ZIP — Folder Structure

When you close a session, the tool downloads a ZIP file named after the session (e.g. `Session_Week-21.zip`).

When you unzip it, here is the exact structure you get:

```
Session_Week-21/
   records.xlsx
   PopUp/
      17-05-2026/
         CarharttWIP_Stories.png
         Edwin_Stories.mp4
      18-05-2026/
         Arte_Stories.png
   Brux/
      18-05-2026/
         CarharttWIP_Edwin_Stories.png
   Vitruta/
      17-05-2026/
         Gramicci_Stories.png
         CarharttWIP_Reels.mp4
   Sneaker_District_AD/
      18-05-2026/
         Ripndip_TheHundreds_Reels.mp4
```

**How to read it:**

| Folder level | Meaning |
|---|---|
| **Top folder** | The session name (e.g. `Session_Week-21`) |
| **Layer 1** | One folder per Business Partner |
| **Layer 2** | One folder per date (DD-MM-YYYY) inside that partner |
| **Files** | Your renamed screenshots/videos at the bottom |

**What to do with it:**

1. Open Google Drive in your browser
2. Open your existing partner folders (PopUp, Brux, etc.)
3. Drag each partner folder from the ZIP into the matching Google Drive folder
4. The dated sub-folders go in cleanly with all files already correctly named

What used to take an hour now takes seconds.

---

## 9. The Excel File Inside the Export

Inside the top folder of the ZIP is a file called `records.xlsx`. Open it in Excel or Google Sheets and you will see a single sheet with these columns — **the exact same columns as your current Google Sheet**:

| Column | Description |
|---|---|
| **Customer** | Business Partner name |
| **Brand** | Comma-joined list of brands (e.g. "CarharttWIP, Edwin") |
| **Number of Posts Uploaded** | How many files were uploaded in that record |
| **Type** | Stories / Reels / Posts |
| **Content Type** | Product IMG / Campaign IMG/VID / etc. |
| **Content Source** | Brand / Customer / Others (?) |
| **Link to Content** | The renamed filenames, comma-separated |
| **Date** | DD-MM-YYYY |

Example rows:

| Customer | Brand | # Posts | Type | Content Type | Source | Link to Content | Date |
|---|---|---|---|---|---|---|---|
| PopUp | CarharttWIP | 1 | Stories | Product IMG | Customer | CarharttWIP_Stories.png | 17-05-2026 |
| Brux | CarharttWIP, Edwin | 1 | Stories | Product IMG | Customer | CarharttWIP_Edwin_Stories.png | 18-05-2026 |
| Vitruta | Gramicci | 1 | Stories | Product IMG | Brand | Gramicci_Stories.png | 17-05-2026 |

You can paste this directly into your master Google Sheet, or send it as-is.

---

<div class="page-break"></div>

## 10. Monthly Summary Report

At the bottom of the Dashboard there is an **orange card** labeled "Monthly Summary Report." This is for end-of-month reporting to your manager.

### How to Generate

1. Open the Dashboard
2. Scroll to the Monthly Summary Report card
3. Pick a **Month** from the dropdown (e.g. May)
4. Pick a **Year** from the dropdown (e.g. 2026)
5. Click **"Export Report"**

A file named `MonthlyReport_May_2026.xlsx` downloads to your computer.

### What's Inside the Excel

The file has **4 sheets** (tabs at the bottom of Excel):

**Sheet 1 — Overview**

A summary panel:

| Metric | Value (example) |
|---|---|
| Report Period | May 2026 |
| Total Records | 87 |
| Total Posts Tracked | 245 |
| Active Business Partners | 18 |
| Active Brands | 9 |

**Sheet 2 — By Business Partner**

One row per partner that was active in the chosen month. Sorted by total posts descending.

| Column | Meaning |
|---|---|
| Business Partner | Customer name |
| Brands Posted | Comma-joined list of every brand they featured |
| # of Brands | Count of distinct brands |
| Total Posts | Sum of all posts they uploaded |
| Stories / Reels / Posts | Breakdown by post type |
| Upload Sessions | How many separate records you logged |
| Active Days | Distinct days they posted |

**Sheet 3 — By Brand**

The same idea, flipped — one row per brand. Sorted by total posts.

| Column | Meaning |
|---|---|
| Brand | Brand name |
| Total Posts | Sum of posts featuring that brand |
| # of Partners | Count of partners that carried it |
| Partners | Comma-joined list of partner names |
| Stories / Reels / Posts | Type breakdown |

**Sheet 4 — All Records**

Every individual record from that month in the standard Google Sheet format (same columns as the session export). This is the full audit trail.

### When to use this

End of every month. Two clicks gives you a complete report showing:

- Which Business Partners were active
- What brands each one posted
- How many posts each one uploaded
- Plus the full row-by-row audit

Attach to an email and send.

---

<div class="page-break"></div>

## 11. Managing Sessions

Past sessions never disappear. You can revisit them anytime from the **Sessions** room.

### Re-Downloading a Past Session

Sometimes a teammate loses the ZIP file, or you need to re-share an old export. To get it back:

1. Click **"Sessions"** in the sidebar
2. Find the session card you want
3. Click **"Download Export"** on that card

The ZIP regenerates fresh with the exact same content as the original export.

### Deleting a Session

If a session was created by mistake or contains test data you want to remove:

1. Click **"Sessions"**
2. Find the session card
3. Click the trash icon
4. Confirm the deletion

> **Warning:** Deleting a session removes:
> - The session itself
> - All records inside the session
> - All uploaded files on disk for those records
>
> This cannot be undone. Make sure you really want to delete before confirming.

---

## 12. Searching &amp; Filtering Records

When you need to find old uploads without going through sessions, use the **Records** page.

It shows every record from every session in one big sortable table. Filters at the top:

| Filter | What it does |
|---|---|
| **Customer** | Type a name (or partial) to show only that customer's rows |
| **Brand** | Type a brand to show only rows featuring it |
| **From Date** | Start of the date range |
| **To Date** | End of the date range |
| **Session** | Dropdown to limit results to a specific session |

You can combine filters — e.g. "show me every Vitruta + Gramicci record from March 2026."

Click any column header to sort by that column. Click the trash icon on a row to delete that one record.

---

<div class="page-break"></div>

## 13. Backup &amp; Data Storage

All your data lives in two folders inside the tool's directory on your laptop:

```
social-media-tracker/
   data/
      tracker.db          ← every record you have ever logged
   uploads/
      {many folders}      ← all your renamed screenshots/videos
```

**To back up:** Copy these two folders to an external drive, USB stick, or cloud backup once a week. That's the complete backup.

**To restore on a new computer:** Install the tool fresh on the new machine, then copy these two folders into the new tool's directory. All your sessions, records, and files come right back.

**To delete everything:** Quit the app, delete the `data/` and `uploads/` folders, restart the app. The tool starts fresh.

> **The tool never deletes anything on its own.** Sessions and records remain in the database permanently until you choose to delete them.

---

## 14. Troubleshooting — Common Problems &amp; Fixes

| Problem | Cause | Fix |
|---|---|---|
| Can't upload — "No active session" message | No session is open | Go to Dashboard, start a new session |
| Brand chip won't add | You forgot to press Enter | Type the brand, then press Enter or click a suggestion |
| Wrong date on a record | You forgot to change the date before submitting | Delete the record, re-upload with correct date |
| Session won't close | Network or browser issue | Refresh the page and try again — your data is safe |
| ZIP didn't download | Browser blocked the popup | Allow popups for localhost in your browser settings |
| Excel column order wrong | You opened a different file | Open `records.xlsx` from inside the session ZIP, not from elsewhere |
| Customer/brand suggestion missing | First time you've used that name | The tool remembers it after the first upload |
| File too large to upload | Single file over 4 MB | Compress the video or split into multiple uploads |
| Need to recover a deleted record | Deletion is permanent | Restore from your backup of `data/` and `uploads/` |
| Tool won't start (port in use) | Another app on port 3000 | Quit other apps using that port, or restart your laptop |

---

<div class="page-break"></div>

## 15. Quick Reference Card

A one-page cheat sheet to keep next to your laptop.

```
DAILY WORKFLOW
─────────────────────────────────────────────
1. Dashboard → Start New Session (name it)
2. Upload page → for each finding:
     • Drag screenshots
     • Pick Customer (autocomplete)
     • Add Brand chips (press Enter to add)
     • Set Date / Type / Content Type / Source
     • Click "Upload & Save"
3. Dashboard → "Close & Export Session"
4. ZIP downloads → drag folders to Google Drive
```

```
FILE NAMING RULE
─────────────────────────────────────────────
1 brand:    {Brand}_{PostType}.ext
2+ brands:  {Brand1}_{Brand2}_..._{PostType}.ext
Multi-file: append _1, _2, _3 before extension

Examples:
   CarharttWIP_Stories.png
   CarharttWIP_Edwin_Reels.mp4
   Gramicci_Market_Twojeys_HUF_Stories.png
   CarharttWIP_Stories_1.png  (when 3+ files)
```

```
EXPORT FOLDER STRUCTURE
─────────────────────────────────────────────
Session_{Name}/
   records.xlsx
   {Customer}/
      {DD-MM-YYYY}/
         {Brand_PostType}.ext
```

```
MONTHLY REPORT
─────────────────────────────────────────────
Dashboard → orange card at bottom
→ Pick Month + Year → Click "Export Report"
→ 4-sheet Excel: Overview / By Partner / By Brand / All
```

```
BACKUP
─────────────────────────────────────────────
Copy these two folders weekly:
   social-media-tracker/data/
   social-media-tracker/uploads/
```

---

<div class="page-break"></div>

## 16. Appendix A — Field Reference

Complete list of every field in the system and where it lives.

| Field | Where Set | Stored As | Used In |
|---|---|---|---|
| Session Name | Dashboard "Start New Session" input | Plain text | ZIP folder name |
| Session Status | Auto-set by tool | open / closed | Filters, badges |
| Customer | Upload form combobox | Plain text | Folder name in ZIP, Excel column |
| Brands | Upload form multi-tag picker | JSON array | Filename, Excel column |
| Date | Upload form date picker | DD-MM-YYYY | Sub-folder name in ZIP, Excel column |
| Type | Upload form dropdown | Stories / Reels / Posts | Filename suffix, Excel column |
| Content Type | Upload form dropdown | One of 5 values | Excel column |
| Content Source | Upload form dropdown | Brand / Customer / Others (?) | Excel column |
| Files | Drag-drop in Upload | Renamed and stored on disk | Files in ZIP |
| Number of Posts | Auto-calculated | Count of files in record | Excel column |
| Created At | Auto-stamped | ISO timestamp | Sorting in Records page |

---

## 17. Appendix B — Full Feature List

Everything the tool supports, in one list.

**Sessions**

- Manual start with custom name
- Manual close that triggers export
- One open session enforced at a time
- Re-download export for any past session
- Delete a session (cascades to files + records)
- View session detail page with embedded records table

**Upload**

- Drag-and-drop or click-to-browse
- Multiple files per upload session
- Image and video files supported
- Automatic file renaming following `Brand_PostType` rule
- Multi-brand tagging via chip picker
- Customer and Brand autocomplete from past entries
- Required-field validation before submit
- Session gating (cannot upload without open session)

**Records**

- Full sortable table of every record across all sessions
- Filter by Customer, Brand, date range, and session
- Pagination for large datasets
- Inline delete per row
- Multi-brand chips rendered in the Brand column
- Type displayed as a colored pill (Stories blue, Reels purple, Posts green)

**Exports**

- Per-session ZIP with `Customer/Date/files` structure
- Per-session Excel matching the Google Sheet column format
- Monthly Summary Report with 4 sheets (Overview, By Partner, By Brand, All Records)
- Re-export from Sessions room anytime
- All exports include the renamed filenames

**Dashboard**

- Active session hero card with live record + file counts
- Quick "Start New Session" inline form
- 4 stat cards: Total Records, Active Customers, Posts This Month, Most Active Brand
- 30-day upload activity line chart
- Top Brands horizontal bar list
- Monthly Summary Report card with month/year picker
- Recent Uploads preview table

**Data**

- Local SQLite database (no cloud, no account)
- Permanent storage — nothing auto-deletes
- Customer and Brand lookup tables grow as you use the tool
- Backup by copying two folders (`data/` and `uploads/`)
- Full data ownership — everything on your laptop

**Interface**

- Light-theme modern dashboard design
- Sidebar navigation with 4 main rooms
- Plus Jakarta Sans + Inter typography
- Blue gradient accents
- Responsive cards and tables
- Works offline once installed (no internet needed for daily use)
