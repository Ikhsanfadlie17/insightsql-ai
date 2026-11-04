"""Generate a deterministic, realistic client-services dataset for demos and tests.

The data deliberately contains the problems real exports have: missing IDs and
revenue, exact duplicate rows, inconsistent client names, Indonesian headers,
and (in the CSV) day-first dates. Client lifecycles are constructed so that
every retention category (new, returning, reactivated, not returned) exists.

Usage:
    python -m sample_data.generate_sample_data            # writes into sample_data/
    python -m sample_data.generate_sample_data --seed 7 --out /tmp/x
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

COLUMNS: tuple[str, ...] = (
    "ID Klien",
    "Nama Klien",
    "Tanggal Layanan",
    "Departemen",
    "Jenis Layanan",
    "Pendapatan (Rp)",
    "Status",
)

YEARS: tuple[int, ...] = (2023, 2024, 2025, 2026)
DATA_CUTOFF = date(2026, 10, 31)  # 2026 is a partial year, like real "current year" data.

DEPARTMENTS: dict[str, list[tuple[str, int]]] = {
    # department -> [(service type, base revenue in Rupiah)]
    "Konsultasi Bisnis": [("Konsultasi Strategi", 45_000_000), ("Riset Pasar", 30_000_000)],
    "Audit": [("Audit Keuangan", 60_000_000), ("Audit Internal", 40_000_000)],
    "Perpajakan": [("Konsultasi Pajak", 15_000_000), ("Pelaporan SPT", 8_000_000)],
    "Pelatihan": [("Pelatihan Karyawan", 12_000_000), ("Workshop Manajemen", 20_000_000)],
    "Teknologi Informasi": [("Implementasi ERP", 90_000_000), ("Audit Sistem TI", 35_000_000)],
}
DEPARTMENT_INFO: list[dict[str, str]] = [
    {"Departemen": "Konsultasi Bisnis", "Kepala Departemen": "Rina Wijaya", "Lokasi": "Jakarta"},
    {"Departemen": "Audit", "Kepala Departemen": "Budi Santoso", "Lokasi": "Jakarta"},
    {"Departemen": "Perpajakan", "Kepala Departemen": "Sari Lestari", "Lokasi": "Surabaya"},
    {"Departemen": "Pelatihan", "Kepala Departemen": "Agus Pratama", "Lokasi": "Bandung"},
    {
        "Departemen": "Teknologi Informasi",
        "Kepala Departemen": "Dewi Anggraini",
        "Lokasi": "Jakarta",
    },
]
STATUSES: tuple[str, ...] = ("Selesai", "Proses", "Dibatalkan")
STATUS_WEIGHTS: tuple[float, ...] = (0.82, 0.12, 0.06)

_NAME_WORDS = (
    "Maju", "Jaya", "Sentosa", "Abadi", "Nusantara", "Sejahtera", "Mandiri", "Prima",
    "Global", "Karya", "Mitra", "Sinar", "Cahaya", "Berkah", "Utama", "Makmur",
)  # fmt: skip
_NAME_SECTORS = (
    "Logistik", "Teknologi", "Farmasi", "Konstruksi", "Pangan", "Energi", "Retail",
    "Media", "Agro", "Finansial",
)  # fmt: skip

# Lifecycle patterns: which of YEARS a client is active in. Fixed groups guarantee
# every retention category exists; the remaining clients get random patterns.
_FIXED_PATTERNS: list[tuple[tuple[int, ...], int]] = [
    ((2025, 2026), 25),  # returning 2025 -> 2026
    ((2023, 2024, 2025, 2026), 15),  # long-standing
    ((2026,), 20),  # new in 2026
    ((2025,), 15),  # not returned in 2026
    ((2024, 2025), 10),  # not returned in 2026, older
    ((2024, 2026), 8),  # reactivated in 2026
    ((2023, 2026), 4),  # reactivated after a longer gap
    ((2023,), 8),  # only seen in 2023
]
N_RANDOM_CLIENTS = 45


def _client_names(rng: np.random.Generator, n: int) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    while len(names) < n:
        prefix = rng.choice(["PT", "CV"], p=[0.8, 0.2])
        name = f"{prefix} {rng.choice(_NAME_WORDS)} {rng.choice(_NAME_SECTORS)}"
        if name in seen:
            name = f"{name} {rng.choice(_NAME_WORDS)}"
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _name_variant(name: str, rng: np.random.Generator) -> str:
    """Return a realistic inconsistent spelling of a client name."""
    variants = [name.upper(), name.replace("PT ", "PT. "), f" {name}  ", name.lower()]
    return str(rng.choice(variants))


def _random_date(rng: np.random.Generator, year: int) -> date:
    start = date(year, 1, 1)
    end = DATA_CUTOFF if year == DATA_CUTOFF.year else date(year, 12, 31)
    return start + timedelta(days=int(rng.integers(0, (end - start).days + 1)))


def _lifecycles(rng: np.random.Generator) -> list[tuple[int, ...]]:
    patterns = [pattern for pattern, count in _FIXED_PATTERNS for _ in range(count)]
    for _ in range(N_RANDOM_CLIENTS):
        first = int(rng.choice(YEARS))
        active = [first] + [y for y in YEARS if y > first and rng.random() < 0.6]
        patterns.append(tuple(active))
    order = rng.permutation(len(patterns))
    return [patterns[i] for i in order]


def generate(seed: int = 42) -> pd.DataFrame:
    """Build the transactions table.

    Args:
        seed: Random seed; the same seed always yields the identical DataFrame.

    Returns:
        DataFrame with the columns in ``COLUMNS``; ``Tanggal Layanan`` is datetime64.
    """
    rng = np.random.default_rng(seed)
    lifecycles = _lifecycles(rng)
    names = _client_names(rng, len(lifecycles))
    inconsistent = set(rng.choice(len(lifecycles), size=8, replace=False).tolist())
    departments = list(DEPARTMENTS)

    rows: list[dict[str, object]] = []
    for idx, (years, name) in enumerate(zip(lifecycles, names, strict=True)):
        client_id = f"KL-{idx + 1:04d}"
        home_dept = str(rng.choice(departments))
        for year in years:
            for _ in range(1 + int(rng.poisson(1.6))):
                dept = home_dept if rng.random() < 0.8 else str(rng.choice(departments))
                service, base = DEPARTMENTS[dept][int(rng.integers(0, 2))]
                revenue = round(base * float(rng.lognormal(0, 0.35)), -5)
                shown_name = (
                    _name_variant(name, rng) if idx in inconsistent and rng.random() < 0.5 else name
                )
                rows.append(
                    {
                        COLUMNS[0]: client_id,
                        COLUMNS[1]: shown_name,
                        COLUMNS[2]: _random_date(rng, year),
                        COLUMNS[3]: dept,
                        COLUMNS[4]: service,
                        COLUMNS[5]: revenue,
                        COLUMNS[6]: str(rng.choice(STATUSES, p=STATUS_WEIGHTS)),
                    }
                )

    df = pd.DataFrame(rows, columns=list(COLUMNS))
    df[COLUMNS[2]] = pd.to_datetime(df[COLUMNS[2]])
    df[COLUMNS[0]] = df[COLUMNS[0]].astype("object")
    df[COLUMNS[5]] = df[COLUMNS[5]].astype("float64")

    # Data-quality problems found in real exports.
    n = len(df)
    df.loc[rng.choice(n, size=max(3, n // 100), replace=False), COLUMNS[0]] = None
    df.loc[rng.choice(n, size=max(5, n // 30), replace=False), COLUMNS[5]] = np.nan
    duplicates = df.iloc[rng.choice(n, size=max(5, n // 50), replace=False)]
    df = pd.concat([df, duplicates], ignore_index=True)

    df = df.sort_values(COLUMNS[2], kind="stable").reset_index(drop=True)
    return df


def write_outputs(out_dir: Path, seed: int = 42) -> list[Path]:
    """Write the Excel workbook (two sheets) and a day-first CSV.

    Returns:
        Paths of the written files.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    df = generate(seed)

    xlsx = out_dir / "client_services.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Transactions", index=False)
        pd.DataFrame(DEPARTMENT_INFO).to_excel(writer, sheet_name="Departments", index=False)

    csv = out_dir / "client_services.csv"
    csv_df = df.copy()
    csv_df[COLUMNS[2]] = csv_df[COLUMNS[2]].dt.strftime("%d/%m/%Y")
    csv_df.to_csv(csv, index=False)
    return [xlsx, csv]


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    for path in write_outputs(args.out, args.seed):
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
