import json
import re
import pandas as pd

# ============================
# Читаем документы
# ============================

with open("parsed_documents.json", "r", encoding="utf-8") as f:
    documents = json.load(f)

print(f"Документов: {len(documents)}")

# ============================
# Читаем ledger
# ============================

ledger = pd.read_csv("master_ledger_2025.csv")
print(ledger[ledger["account_id"] == "ACC-7204"])

# Извлекаем scenario_id из txn_id
ledger["scenario_id"] = ledger["txn_id"].str.extract(r"TXN-([A-Z]\d+)")

results = []

# ============================
# Обрабатываем каждый PDF
# ============================

for doc in documents:

    full_text = ""

    # Собираем текст всех страниц
    for page in doc["pages"]:
        if page["text"]:
            full_text += page["text"] + "\n"

    # ============================
    # Поиск account_id
    # ============================

    account_match = re.search(r"ACC-\d+", full_text)

    if account_match:
        account_id = account_match.group()
    else:
        account_id = None

    # ============================
    # Поиск borrower_name
    # ============================

    borrower_name = None

    for line in full_text.split("\n"):

        line = line.strip()

        if (
            "JSC" in line
            or "LLP" in line
            or "Ltd" in line
            or "Limited" in line
        ):
            borrower_name = line
            break

    # ============================
    # Поиск scenario_id
    # ============================

    scenario_id = None

    if account_id is not None:

        match = ledger[ledger["account_id"] == account_id]

        if not match.empty:
            scenario_id = match.iloc[0]["scenario_id"]

            if pd.isna(scenario_id):
                scenario_id = None

    # ============================
    # Сохраняем результат
    # ============================

    results.append({
        "filename": doc["filename"],
        "account_id": account_id,
        "borrower_name": borrower_name,
        "scenario_id": scenario_id
    })

# ============================
# Сохраняем JSON
# ============================
results = sorted(
    results,
    key=lambda x: (
        x["account_id"] is None,  # сначала записи с account_id
        x["filename"]             # затем по имени файла
    )
)

with open("stage2_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=4)

print("=" * 60)
print("Stage 2 completed!")
print(f"Обработано документов: {len(results)}")
print("Результат сохранён в stage2_results.json")