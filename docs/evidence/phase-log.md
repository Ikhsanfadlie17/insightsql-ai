# Phase log

Ringkasan per fase sebagai evidence. Detail desain ada di `docs/ARCHITECTURE.md`, detail
deviasi dan review ada di file plan (`docs/plans/...`). Semua tanggal 2026-11-04.
Jumlah test = hasil `pytest` penuh terakhir yang tercatat pada akhir fase.

---

## Phase 1 — Desain

- **Tujuan:** memahami PRD, merancang arsitektur dan rencana kerja sebelum menulis kode.
- **Commit:** `ec77482`..`5d7a1b2`.
- **Hasil:** `docs/ARCHITECTURE.md` (arsitektur, aliran data, model keamanan, definisi
  retensi), rencana 26 task dalam Phase 2–8. Keputusan dengan user: eksekusi native,
  review independen tiap akhir fase, kategori Reactivated & Between-period, Existing Active
  sebagai agregat terpisah, UI/dokumen bahasa Inggris dengan tanya-jawab ID/EN, fake LLM
  sampai API tersedia (tanpa benchmark palsu).
- **Keputusan:** D1–D11 (ARCHITECTURE §10).
- **Test:** tidak ada kode.

## Phase 2 — Fondasi (Task 1–6)

- **Tujuan:** skeleton FastAPI/Streamlit, dataset sampel, upload → normalisasi → inferensi
  tipe → DuckDB, metadata SQLite, halaman Data Sources.
- **Commit:** `df1581e`..`71b2b81`.
- **Test:** 149 lulus, 0 gagal (akhir fase, setelah perbaikan review).
- **Review independen:** perbaikan H1–H4, M2, M3, M5, L1, L2, L6 (commit `65d0a67`),
  antara lain presisi integer besar, desimal Excel terbaca sebagai ribuan, batas UTF-8 pada
  sampel, crash Overview. Follow-up yang diterima: M1, M4, L4, L5, L7 (lihat plan).
- **Masalah teknis tercatat:** folder `pages/` membuat Streamlit menjalankan halaman secara
  langsung → dipindah ke `frontend/views/` + regression test; override font CSS global
  merusak ikon → font lewat `.streamlit/config.toml`.
- **Keterbatasan:** batas ukuran upload ditegakkan setelah body di-spool (deployment).

## Phase 3 — Data Intelligence (Task 7–8)

- **Tujuan:** profiling di DuckDB, saran peran kolom, mapping yang dikonfirmasi user,
  opsi format angka/urutan tanggal, catatan per kolom (tidak ada perubahan diam-diam).
- **Commit:** `8e21831`..`49c3080`.
- **Test:** 290 lulus, 0 gagal.
- **Review independen:** 0 Critical, 2 High, 3 Medium, 10 Low — semua High/Medium dan
  10 Low diperbaiki (commit `4e39a8d`). Contoh: ID/NIK berspasi kehilangan nol di depan;
  ID numerik terkonversi; format `1.500`/`1,500` campuran; mata uang campuran; `Rp …,-`.
- **Diterima:** duplikat dihitung setelah konversi tipe (L1 sebagian); `Rp 0123` → 123;
  ganti format angka = import ulang.

## Phase 4 — Integrasi AI (Task 9–13)

- **Tujuan:** validator SQL (SQLGlot), executor sandbox, klien LLM + pencatatan biaya,
  konteks skema yang aman privasi, pipeline Text-to-SQL dengan repair terbatas.
- **Commit:** `2de41c3`..`6afcea3`.
- **Test:** 558 lulus, 0 gagal. Korpus serangan: 102 kasus SQL berbahaya, semuanya ditolak
  dengan aturan yang diharapkan (`tests/security/`). `LLM: mock`.
- **Review independen (fokus keamanan SQL):** 0 Critical, 3 High, 4 Medium, ~9 Low.
  - H1 katalog sistem terbaca lewat table macro / CTE di luar scope → resolusi tabel per scope, semua table function ditolak.
  - H2 generator mengabaikan timeout & batas memori (test sempat hang) → worker proses terisolasi (`python -I`) yang di-kill saat timeout/RSS, ditambah RLIMIT_AS.
  - H3 nilai sel bocor lewat pesan error → hanya pesan berkategori.
  - M1–M4 (crash tokenizer, kegagalan tak tercatat, token tertagih hilang, privasi konteks) diperbaiki.
- **Diterima:** fungsi skalar difilter denylist (bukan allowlist); watchdog RSS hanya Linux;
  self-join equality dibatasi oleh worker.
- **Keterbatasan:** API OpenAI sungguhan **belum diuji** (jaringan diblokir, belum ada key).

## Phase 5 — Analytics (Task 14–18)

- **Tujuan:** retensi klien deterministik (fokus utama: kebenaran angka), dashboard Client
  Analytics, visualisasi, insight yang terverifikasi, AI Analyst end-to-end.
- **Commit:** `4975caf`..(akhir fase, lihat git log).
- **Komponen baru:** `analytics_service.py` (template SQL tetap → validator → worker
  sandbox, cache per dataset+mapping), `visualization_service.py`, `insight_generator.py`,
  `analysis_orchestrator.py`, `history_repo.py`, API `client-analytics/*`, `/analyses/ask`,
  `/conversations`; halaman Client Analytics & AI Analyst; format angka Indonesia.
- **Keputusan:** D12–D17 (ARCHITECTURE §10). Definisi metrik, rumus, denominator, asumsi,
  dan keterbatasan: ARCHITECTURE §8 & §8.1. Alur AI Analyst: §5.2. Verifikasi insight: §7.4.
- **Test:** 725 lulus sebelum review → **757 lulus, 0 gagal** setelah perbaikan
  (`.venv/bin/python -m pytest -q`, ±2 menit). Ruff check + format bersih.
- **Bukti kebenaran angka:**
  - Fixture kecil yang dihitung manual (fixture dari plan dan 20+ kasus tepi: batas tanggal,
    datetime 23:59:59, ID kosong/berspasi/NBSP/ZWSP, duplikat, tanggal invalid, periode tidak
    bersebelahan, YTD 29 Feb, histori mulai di tengah aktivitas).
  - Implementasi pandas independen (`tests/ground_truth.py`) untuk setiap pasangan tahun
    2023–2026 dan YTD pada dataset sampel.
  - Mutation check template SQL: mutan non-ekuivalen tertangkap test.
  - Rekalkulasi independen dari CSV mentah: 0 selisih
    ([experiments/2026-11-04-phase5-retention-recompute.md](experiments/2026-11-04-phase5-retention-recompute.md)).
- **E2E (LLM: mock):** backend + Streamlit + DuckDB sungguhan dengan LLM palsu berskrip
  ([experiments/2026-11-04-phase5-e2e-fake-llm.md](experiments/2026-11-04-phase5-e2e-fake-llm.md),
  `scripts/e2e/`). Saat e2e diulang, ditemukan bug yang tidak terlihat di AppTest:
  Streamlit mencocokkan nilai selectbox berdasarkan label, sehingga follow-up masuk ke
  percakapan lama yang judulnya sama. Diperbaiki (label unik + pilihan via Session State)
  dan ditambah regression test.
- **Review independen:** 1 Critical, 2 High, 8 Medium, 10 Low; seluruh Critical/High/Medium
  diperbaiki, Low sebagian besar diperbaiki (rincian di plan, "Phase 5 independent review").
  | Temuan | Sebelum | Sesudah |
  |---|---|---|
  | C1 | Contoh NIK di catatan kolom identitas ikut ke prompt insight | Hanya teks tetap per kode warning; test memastikan NIK tidak terkirim |
  | H1 | LLM bisa menulis rate N/A sebagai "0%" | Persen hanya cocok dengan sumber persentase nyata; fakta retensi selalu dari template |
  | H2 | "naik 20%" lolos untuk penurunan | Perubahan menyimpan tanda; kata naik/turun dicek |
  | M1–M8 | bilangan kecil sebagai %, kata kausal berimbuhan, ID numerik panjang, pembulatan chart, garis tidak urut, heuristik KPI, flag YTD diabaikan, ZWSP/BOM | Diperbaiki + test |
- **Pengukuran (bukan benchmark):** satu `compare_periods` pada sampel ±276 ms (didominasi
  start proses worker). Reviewer mengukur 3 juta baris: compare ±4,9 s, halaman klien ±8,6 s
  (sebelum perbaikan window count) — masih di bawah timeout 10 s.
- **Token/biaya:** tidak ada data nyata. E2E memakai LLM palsu dengan token tetap; harga di
  `config/pricing.json` kosong, sehingga biaya tercatat "tidak tersedia".
- **Keterbatasan:** API OpenAI sungguhan belum diuji; grounding membuktikan angka *dapat
  diturunkan* dari hasil, bukan bahwa angka melekat pada metrik yang benar; angka dalam
  bentuk kata tidak dicek; tabel hasil (`st.dataframe`) belum berformat Indonesia; duplikat
  terdeteksi setelah konversi tipe.


## Phase 6 — Product Features (Task 19–21)

- **Tanggal:** 2026-11-04 (Task 19–20) dan 2026-11-05 (Task 21, review), Windows lokal.
- **Tujuan:** riwayat & manajemen percakapan, export, halaman Settings, manajemen dataset,
  penyempurnaan UI.
- **Commit:** `dff19dd` (Task 19), `f7554f6` (Task 20), `2c3bcec` + `df17da2` (Task 21),
  `090bacc` (perbaikan review).
- **Komponen baru:** `api/conversations.py` (`GET /conversations`, `GET/DELETE
  /conversations/{id}`, `GET /analyses/recent`), `export_service.py` + `api/exports.py`
  (CSV/Excel/PDF/SQL), `api/settings.py` (`GET /settings`), halaman Query History & Settings,
  rename/hapus dataset di Data Sources, tombol unduh di AI Analyst & Client Analytics,
  `scripts/e2e/all_pages.js`.
- **Keputusan & deviasi** (rinci di plan, "Phase 6 as built"): recent activity dari
  `GET /analyses/recent`; export dibuat saat tombol diklik; CSV menetralkan awalan formula;
  `/settings` hanya field whitelist dan `api_key_set` ya/tidak; konfirmasi hapus inline
  (expander Streamlit menyembunyikannya); deteksi tipe dari sampel 500 baris saat inspect
  untuk peringatan "Keep as text"; envelope `{"error": ...}` juga untuk error validasi &
  route tak dikenal; pembersihan upload berkala.
- **Test:** 757 (akhir Phase 5) → 810 (Task 19–20) → 854 sebelum review → **862 lulus,
  1 skip** setelah perbaikan (`.venv\Scripts\python -m pytest -q`, ±2,5 menit, Windows; skip =
  test watchdog memori yang hanya berlaku di Linux). Ruff check + format bersih.
- **Keamanan yang diuji:** key (utuh dan potongan) tidak pernah ada di respons `/settings`
  maupun log; path server tidak bocor; error validasi tidak mengulang input; injeksi formula
  CSV/Excel dinetralkan; teks dari file di-escape sebelum masuk Markdown.
- **E2E (LLM: mock):** enam halaman × desktop/HP, 12/12 lolos, 0 error browser
  ([experiments/2026-11-05-phase6-e2e-all-pages.md](experiments/2026-11-05-phase6-e2e-all-pages.md)).
  Empat masalah hanya terlihat di aplikasi yang berjalan/screenshot (konfirmasi hapus
  tersembunyi, tabel terpotong di HP, statistik profil terpotong & format Inggris, rute
  `/overview` di skrip) — semuanya diperbaiki.
- **Review independen:** 0 Critical, 1 High, 0 Medium, 4 Low; semuanya diperbaiki kecuali
  satu saran opsional (lihat tabel).
  | Temuan | Sebelum | Sesudah |
  |---|---|---|
  | H1 | Inspect Excel mem-parse workbook sekali per sheet (≈S²); 8 sheet × 40k baris 20,9 s | Satu `read_excel` untuk semua sheet: 4,3 s, tipe identik ([benchmark](experiments/2026-11-05-phase6-inspect-benchmark.md)); timeout inspect 120 s |
  | L1 | Header tanggal/"NA" tidak cocok dengan kunci hint; error hint → 500 | Kunci = teks header di form; kegagalan hint → `{}` |
  | L2 | Cleanup berkala bisa menghapus upload yang sedang di-import | `create_dataset` memperbarui umur upload; cleanup melewati upload yang import-nya belum selesai |
  | L3 | Pecahan kecil tampil 0 (0,0045 → "0,00") | 3 digit signifikan di PDF & profil |
  | L4 | Hapus dataset yang filenya terbuka → 500 | 422 "in use, try again" |
  | L4 (opsional) | Tolak hapus dataset `processing` di backend | Tidak diambil: hapus saat ingest memang didukung sejak Phase 2 (ingest membersihkan diri); UI tetap menonaktifkan tombol |
- **Token/biaya:** tidak ada data nyata (LLM palsu, harga kosong → biaya "N/A").
- **Keterbatasan:** API OpenAI sungguhan belum diuji; hint tipe CSV selalu memakai format
  angka/tanggal "auto" (bukan pilihan di form); inspect Excel masih ±2× biaya sebelum
  Phase 6 dan workbook 50 MB tidak diukur; PDF memakai font bawaan (karakter non-Latin
  diganti `?`); pembersihan berkala hanya untuk upload mentah, bukan dataset.
