# Evidence log

Bahan mentah untuk dokumentasi final (technical case study). Ini **bukan** dokumentasi
final: catatan di sini ditulis selama pengembangan agar setiap klaim nantinya bisa
dibuktikan dari isi repository, bukan dari ingatan atau percakapan.

| File | Isi |
|---|---|
| [`phase-log.md`](phase-log.md) | Satu bagian per fase (ditambah, tidak ditulis ulang): tujuan, commit, komponen, test, temuan review, pengukuran, keterbatasan. |
| [`experiments/`](experiments/) | Satu file per eksperimen yang **benar-benar dijalankan**: tanggal, setup, perintah, label LLM, hasil. |
| [`screenshots/`](screenshots/) | Tangkapan layar aplikasi yang benar-benar berjalan (data sampel sintetis). |

Sumber lain yang tidak diduplikasi di sini:
- Masalah & kebutuhan: [`../PRD.md`](../PRD.md)
- Arsitektur, model keamanan, definisi metrik, keputusan desain D1–D17, risiko:
  [`../ARCHITECTURE.md`](../ARCHITECTURE.md)
- Rencana, deviasi per fase, hasil review per fase:
  [`../plans/2026-11-04-insightsql-implementation-plan.md`](../plans/2026-11-04-insightsql-implementation-plan.md)
- Korpus serangan SQL: [`../../tests/security/attack_corpus.yaml`](../../tests/security/attack_corpus.yaml)
- Skrip e2e yang bisa direproduksi: [`../../scripts/e2e/`](../../scripts/e2e/)

## Aturan

1. **Label LLM wajib** pada setiap hasil yang melibatkan LLM:
   - `LLM: none` — tidak ada panggilan LLM (deterministik).
   - `LLM: mock` — `FakeLLMClient` / LLM palsu berskrip. Membuktikan kode kita, **bukan** kualitas model.
   - `LLM: real (<model id>, <tanggal>)` — API model sungguhan.
2. **Tidak ada angka tanpa eksperimen.** Akurasi, benchmark, latency, token, dan biaya hanya
   dicatat jika diukur; sertakan perintah dan lingkungan. Yang tidak diukur ditulis
   "tidak diukur" / "tidak tercatat".
3. **Tidak ada secret, API key, data pribadi, atau dataset sensitif.** Hanya data sampel
   sintetis (`sample_data/`).
4. **Hemat:** laporan akhir fase = bagian `phase-log.md` fase itu. Eksperimen berat tidak
   dijalankan hanya demi dokumentasi.
5. Bahasa: catatan internal boleh bahasa Indonesia; dokumentasi final nanti bahasa Inggris.

## Status hal yang belum ada (direncanakan)

- Dataset evaluasi Text-to-SQL (Phase 7): `evaluation/questions.yaml` (pertanyaan, skema,
  SQL referensi, hasil yang diharapkan, kategori valid/ambigu/berbahaya) dan hasil per run
  `evaluation/results/<tanggal>-<mock|model>.jsonl`. Metrik: validitas SQL, akurasi
  eksekusi, kebenaran semantik, ketahanan terhadap ambiguitas, keamanan, latency, biaya.
- Uji dengan API OpenAI sungguhan: **belum pernah dijalankan** (jaringan diblokir, belum ada key).
