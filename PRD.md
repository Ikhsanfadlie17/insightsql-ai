# PRODUCT REQUIREMENTS DOCUMENT (PRD)
## InsightSQL AI
**AI-Powered Data Analytics & Business Intelligence Platform**

**Version:** 1.0  
**Product Type:** AI Analytics Web Application  
**Primary Users:** Data Analysts, Business Analysts, Management, Researchers  
**Primary Language:** Indonesian and English  
**AI Provider:** OpenAI API (configurable model)

---

# 1. Executive Summary

InsightSQL AI adalah aplikasi AI Data Analyst yang memungkinkan pengguna melakukan analisis dataset Excel (.xlsx), CSV, dan data tabular lainnya menggunakan bahasa alami (Natural Language).

Pengguna tidak perlu menulis kode SQL atau membuat formula Excel secara manual. Pengguna cukup mengunggah dataset dan mengajukan pertanyaan seperti:

- Berapa jumlah klien yang menggunakan layanan pada tahun 2025 dan 2026?
- Berapa klien yang kembali menggunakan layanan pada tahun 2026?
- Berapa pelanggan baru pada tahun 2026?
- Berapa klien tahun 2025 yang tidak kembali pada tahun 2026?
- Bagaimana tren jumlah layanan setiap bulan?
- Departemen mana yang memiliki jumlah pelayanan tertinggi?
- Apa insight utama dari data tersebut?

AI akan memahami pertanyaan, mengidentifikasi kolom yang relevan, menghasilkan SQL, menjalankan query pada database, dan mengembalikan hasil berupa:

1. Jawaban berbasis data.
2. Tabel hasil analisis.
3. Visualisasi grafik otomatis.
4. Business insights.
5. Rekomendasi berdasarkan hasil analisis.
6. SQL query yang digunakan agar hasil transparan dan dapat diverifikasi.

Prinsip utama: LLM bertindak sebagai interpreter dan query generator. Database bertindak sebagai calculation engine.

# 2. Product Objectives

Tujuan utama project:

1. Membangun aplikasi AI Data Analyst berbasis Text-to-SQL.
2. Mengotomatisasi analisis data Excel dan CSV.
3. Menghasilkan analisis klien, retensi, pertumbuhan, dan tren bisnis.
4. Menghasilkan visualisasi secara otomatis berdasarkan pertanyaan pengguna.
5. Meminimalkan biaya penggunaan LLM API.
6. Menghasilkan aplikasi yang dapat digunakan sebagai portofolio AI Engineer.
7. Menunjukkan implementasi AI Engineering, Data Engineering, Backend Engineering, dan Data Visualization.

# 3. Target Users

### Primary Users
- Data Analyst
- Business Intelligence Analyst
- Business Analyst
- Data Scientist
- Management dan decision makers.

### User Problems
- Kesulitan menganalisis dataset dengan banyak kolom.
- Membutuhkan waktu untuk membuat PivotTable.
- Kesulitan menulis SQL.
- Membutuhkan analisis cepat tanpa coding.
- Kesulitan mengubah hasil analisis menjadi visualisasi dan insight.

# 4. Core Features

## 4.1 Dataset Upload

Pengguna dapat mengunggah:
- Excel (.xlsx)
- CSV (.csv)

Persyaratan:
- Mendukung dataset dengan banyak kolom.
- Mendukung dataset dengan ribuan hingga ratusan ribu baris sebagai target awal.
- Mendeteksi tipe data otomatis.
- Mendeteksi missing values.
- Mendeteksi duplicate records.
- Mendeteksi kolom tanggal.
- Mendeteksi kemungkinan kolom identitas klien.
- Menampilkan preview dataset.
- Menampilkan statistik dasar setiap kolom.

Excel:
- Jika terdapat banyak sheet, tampilkan daftar sheet.
- Pengguna dapat memilih sheet yang ingin dianalisis.
- Setiap sheet diproses sebagai dataset terpisah pada tahap awal.

## 4.2 Automatic Data Profiling

Setelah upload, sistem melakukan profiling:

- Total rows.
- Total columns.
- Missing values.
- Duplicate rows.
- Data types.
- Unique values.
- Numeric statistics.
- Date range.
- Candidate identifier columns.

Tampilkan ringkasan dalam dashboard.

AI dapat membantu memberikan rekomendasi kolom yang berpotensi menjadi:
- Client ID.
- Client Name.
- Transaction Date.
- Service Type.
- Department.
- Revenue.
- Status.

Rekomendasi AI harus dapat dikonfirmasi atau diubah oleh pengguna.

## 4.3 Natural Language to SQL

Pengguna dapat menanyakan sesuatu menggunakan bahasa Indonesia atau Inggris.

Contoh:

"Bandingkan jumlah klien tahun 2025 dan 2026."

"Berapa pelanggan yang kembali menggunakan layanan?"

"Berapa pelanggan baru pada tahun 2026?"

"Departemen mana yang memiliki jumlah layanan terbanyak?"

"Berapa pertumbuhan jumlah pelanggan setiap bulan?"

Alur:

1. Pengguna memasukkan pertanyaan.
2. Sistem mengambil schema dan metadata dataset.
3. Sistem mengirim konteks relevan ke OpenAI API.
4. LLM menghasilkan SQL.
5. SQL divalidasi oleh SQL security validator.
6. SQL dijalankan pada DuckDB.
7. Hasil dikembalikan kepada pengguna.
8. Hasil dikirim ke LLM untuk dibuatkan penjelasan jika diperlukan.

Jangan mengirim seluruh dataset ke LLM.

## 4.4 Client Retention & Classification Analytics

Fitur ini menjadi salah satu fitur unggulan aplikasi.

Pengguna dapat menganalisis pergerakan pelanggan antarperiode.

Contoh perbandingan:
- 2025 vs 2026.
- 2024 vs 2025.
- Periode custom.
- Year-over-year.

Kategori klien:

**A. Returning Clients**
Klien yang memiliki aktivitas pada kedua periode yang dibandingkan.

**B. New Clients**
Klien yang pertama kali muncul dalam seluruh riwayat data pada periode terbaru.

**C. Not Returned / Lapsed Clients**
Klien yang aktif pada periode sebelumnya tetapi tidak memiliki aktivitas pada periode terbaru.

**D. Existing Active Clients**
Klien yang telah memiliki riwayat sebelum periode terbaru dan masih aktif pada periode terbaru.

**E. Lost Clients**
Klien yang tidak aktif pada periode terbaru setelah sebelumnya aktif. Gunakan istilah ini sebagai status observasi, bukan bukti permanen bahwa pelanggan telah berhenti.

**F. Repeat Transactions**
Jumlah transaksi atau pelayanan berulang dari klien yang sama.

Penting:
- Gunakan Client ID sebagai identitas utama.
- Jangan hanya mengandalkan nama perusahaan jika tersedia ID unik.
- Jika ID tidak tersedia, berikan opsi konfigurasi kolom identitas.
- Identitas yang tidak konsisten harus ditandai.
- Definisi kategori harus transparan dan dapat disesuaikan.
- Periode perbandingan harus dapat dipilih pengguna.

Sistem harus dapat menghasilkan:
- Total unique clients per period.
- Returning clients.
- New clients.
- Existing active clients.
- Not returned clients.
- Retention rate.
- New client acquisition rate.
- Client growth rate.
- Repeat transaction count.

Rumus:

Retention Rate:

Returning Clients / Previous Period Active Clients × 100%

New Client Rate:

New Clients / Current Period Active Clients × 100%

Client Growth Rate:

(Current Period Unique Clients - Previous Period Unique Clients) / Previous Period Unique Clients × 100%

Jangan menghitung rasio jika denominator nol. Tampilkan N/A.

## 4.5 Automatic Visualization

Sistem menentukan visualisasi yang sesuai berdasarkan jenis pertanyaan dan hasil query.

Jenis grafik:
- Bar Chart.
- Line Chart.
- Pie Chart.
- Donut Chart.
- Area Chart.
- Horizontal Bar Chart.
- KPI Cards.
- Data Tables.

Contoh:

| Pertanyaan | Visualisasi |
|---|---|
| Jumlah klien per tahun | Bar Chart |
| Tren klien per bulan | Line Chart |
| Distribusi klien per kategori | Donut Chart |
| Perbandingan departemen | Horizontal Bar |
| Retention rate | KPI Card |
| Perbandingan klien 2025–2026 | Grouped Bar |

Gunakan Plotly untuk grafik interaktif.

AI boleh memberikan rekomendasi tipe grafik, tetapi frontend harus memvalidasi struktur data dan tipe grafik sebelum rendering.

## 4.6 Business Insights

Setelah query selesai, sistem menghasilkan penjelasan berdasarkan hasil aktual.

Contoh:

"Pada tahun 2026 terdapat 120 klien aktif. Sebanyak 75 klien merupakan klien yang kembali menggunakan layanan dari tahun sebelumnya, sementara 45 klien merupakan pelanggan baru. Tingkat retensi mencapai 62,5%."

Syarat:
- Semua angka harus berasal dari hasil query.
- Jangan mengarang angka.
- Jangan menyimpulkan sebab-akibat tanpa bukti.
- Jelaskan periode dan definisi metrik.
- Jika data tidak mencukupi, jelaskan keterbatasannya.

## 4.7 Chat History

Pengguna dapat:
- Melihat pertanyaan sebelumnya.
- Melihat hasil analisis sebelumnya.
- Melanjutkan percakapan.
- Menghapus riwayat analisis.

Konteks percakapan harus terikat pada dataset yang benar.

## 4.8 Export Results

Pengguna dapat mengunduh:
- Hasil query dalam CSV.
- Hasil analisis dalam Excel.
- Laporan analisis dalam PDF.
- SQL query yang digunakan.

Export harus menyertakan periode dan definisi metrik jika relevan.

# 5. Technical Architecture

## Recommended Tech Stack

Frontend:
- Streamlit.
- Plotly.
- Custom CSS untuk UI profesional.

Backend:
- Python.
- FastAPI.
- Pydantic.

Data Processing:
- Pandas.
- DuckDB.

AI:
- OpenAI API.
- OpenAI Python SDK.
- Structured Outputs jika didukung model yang digunakan.

SQL Validation:
- SQLGlot.
- Custom SQL validator.

Testing:
- Pytest.

Deployment:
- Docker.
- Docker Compose.

Database:
- DuckDB untuk data analitik.
- SQLite atau PostgreSQL untuk metadata dan user/session management jika diperlukan.

## System Architecture

User
↓
Streamlit Frontend
↓
FastAPI Backend
↓
Dataset Processing & Profiling
↓
Schema Context Builder
↓
OpenAI API
↓
Text-to-SQL Generator
↓
SQL Validation & Security Layer
↓
DuckDB Query Engine
↓
Query Results
↓
Visualization Engine (Plotly)
↓
Business Insights Generator
↓
User Interface

# 6. AI Engineering Requirements

## 6.1 LLM Integration

Gunakan OpenAI API dengan konfigurasi environment variable.

OPENAI_API_KEY=your_api_key
OPENAI_MODEL=your_available_model_id

Jangan mengunci model ID secara permanen di source code.

Model harus dapat diganti melalui environment variable tanpa mengubah aplikasi.

## 6.2 Prompt Engineering

LLM harus mendapatkan:
- User question.
- Table schema.
- Column descriptions.
- Data types.
- Relevant sample values yang sudah diminimalkan.
- Database dialect.
- Business definitions.
- SQL restrictions.

LLM harus menghasilkan structured response:

- SQL query.
- Query explanation.
- Chart recommendation.
- Required metrics.
- Assumptions.
- Potential ambiguity.

Jangan mengandalkan teks bebas yang harus diparsing secara rapuh.

## 6.3 SQL Security

Keamanan wajib diterapkan.

- Hanya izinkan SELECT dan CTE yang aman.
- Tolak INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, ATTACH, COPY, INSTALL, LOAD, dan perintah berbahaya lainnya.
- Tolak multi-statement SQL.
- Batasi execution time.
- Batasi jumlah baris hasil.
- Validasi nama tabel dan kolom.
- Gunakan SQL parser, bukan regex saja.
- Jangan pernah mengeksekusi SQL mentah tanpa validasi.
- Jangan izinkan model mengakses file lokal atau filesystem.
- Jangan mengirim API key ke frontend.

Perlakukan konten dataset sebagai data tidak tepercaya, bukan instruksi untuk AI.

## 6.4 Error Handling

Jika SQL gagal:
1. Tangkap error.
2. Kirim pesan error yang aman ke LLM jika diperlukan.
3. Izinkan maksimal dua kali perbaikan otomatis.
4. Validasi kembali query hasil perbaikan.
5. Jika gagal, tampilkan pesan yang mudah dipahami.

Jangan membuat retry tanpa batas.

## 6.5 Cost Optimization

- Jangan mengirim seluruh dataset ke API.
- Gunakan schema context.
- Gunakan metadata profiling.
- Simpan schema dalam cache selama dataset tidak berubah.
- Pisahkan tugas SQL generation dan insight generation.
- Hindari memanggil LLM untuk operasi yang dapat dihitung dengan Python atau SQL.
- Gunakan model ekonomis untuk tugas rutin.
- Catat penggunaan token dan estimasi biaya setiap request.

# 7. UI/UX Requirements

Desain aplikasi harus modern, profesional, minimalis, dan menyerupai SaaS analytics platform.

### Main Navigation

1. Overview.
2. Data Sources.
3. AI Analyst.
4. Client Analytics.
5. Query History.
6. Settings.

### Overview Dashboard

Menampilkan:
- Dataset aktif.
- Total rows.
- Total columns.
- Total clients.
- Total analyses.
- Recent activity.

### AI Analyst Page

Layout:
- Chat interface.
- Dataset selector.
- Suggested questions.
- SQL query expandable panel.
- Analysis result.
- Visualization area.
- Data table.

### Client Analytics Page

Menampilkan:
- Total clients.
- Returning clients.
- New clients.
- Existing active clients.
- Not returned clients.
- Retention rate.
- Client growth.
- Perbandingan periode.

Gunakan warna visualisasi yang konsisten dan mudah dibaca.

# 8. Non-Functional Requirements

Performance:
- Target respons untuk dataset kecil hingga menengah: beberapa detik, bergantung pada model dan query.
- Gunakan background processing jika import dataset membutuhkan waktu.
- Jangan memblokir UI selama operasi yang panjang.

Reliability:
- Hasil analisis harus dapat direproduksi.
- SQL dan hasil analisis dapat ditelusuri.
- Error harus ditangani dengan baik.

Security:
- API key hanya disimpan di server.
- Upload file harus divalidasi.
- Dataset tidak dikirim secara penuh ke penyedia LLM.
- Gunakan isolasi session/dataset.
- Berikan penghapusan dataset dan metadata.

# 9. Evaluation & Testing

Buat evaluation dataset minimal 50 pertanyaan analitik.

Kategori:
1. Simple aggregation.
2. Filtering.
3. Grouping.
4. Date analysis.
5. Client retention.
6. New vs existing clients.
7. Multi-condition queries.
8. Ambiguous questions.
9. Invalid questions.
10. Complex business analytics.

Metrics:

- SQL Execution Success Rate.
- Query Result Accuracy.
- Semantic Accuracy.
- Average Latency.
- Token Consumption.
- Estimated API Cost.
- Error Recovery Rate.

Target awal proyek:
- SQL Execution Success Rate ≥ 95% pada test set yang terdefinisi.
- Query Result Accuracy ≥ 90%.
- Tidak ada eksekusi SQL yang melanggar aturan keamanan pada pengujian keamanan.
- Seluruh KPI utama cocok dengan ground-truth dataset.

Jangan mengklaim target telah tercapai sebelum pengujian benar-benar dilakukan.

# 10. Development Phases

Phase 1 — Foundation:
- Setup repository.
- Setup environment.
- Upload Excel/CSV.
- Data profiling.
- DuckDB integration.

Phase 2 — AI Integration:
- OpenAI integration.
- Schema context.
- Text-to-SQL.
- SQL validation.
- Query execution.

Phase 3 — Analytics:
- Client retention.
- New vs existing.
- Business metrics.
- Automatic visualization.
- Business insights.

Phase 4 — Product Features:
- Chat history.
- Export.
- Dataset management.
- UI refinement.

Phase 5 — Quality & Deployment:
- Unit testing.
- Integration testing.
- Security testing.
- Evaluation benchmark.
- Docker.
- Deployment.
- Documentation.

# 11. Portfolio Deliverables

Hasil akhir harus mencakup:

1. Working web application.
2. GitHub repository.
3. README.md lengkap.
4. PRD.md.
5. Architecture diagram.
6. Database schema documentation.
7. AI prompt documentation.
8. Evaluation dataset.
9. Evaluation results.
10. Unit and integration tests.
11. Docker configuration.
12. Demo video.
13. Screenshots aplikasi.
14. Deployment instructions.

# 12. Definition of Done

Project dianggap selesai apabila:

- Pengguna dapat mengunggah Excel dan CSV.
- Sistem dapat membaca dataset dengan banyak kolom.
- Pengguna dapat bertanya dalam bahasa Indonesia.
- AI dapat menghasilkan SQL yang benar.
- SQL divalidasi sebelum dieksekusi.
- Sistem dapat menghitung metrik klien 2025–2026.
- Sistem dapat membedakan klien baru, berulang, existing, dan tidak kembali.
- Sistem dapat menghasilkan grafik otomatis.
- Sistem menghasilkan business insights berdasarkan angka aktual.
- Hasil dapat diekspor.
- API cost dapat dipantau.
- Aplikasi memiliki dokumentasi dan dapat dijalankan oleh orang lain.

---

**Product Vision:**  
"Making data analytics accessible through natural language, powered by reliable SQL execution and intelligent business insights."
