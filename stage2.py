import json
import re

from src.ledger import LedgerService

# =====================================
# Читаем документы
# =====================================

with open("parsed_documents.json", "r", encoding="utf-8") as f:
    documents = json.load(f)

print(f"Документов: {len(documents)}")

# =====================================
# Инициализация LedgerService
# =====================================

ledger = LedgerService("master_ledger_2025.csv")
ledger.initialize()

results = []

# =====================================
# Обрабатываем документы
# =====================================

for doc in documents:

    full_text = ""

    for page in doc["pages"]:
        if page["text"]:
            full_text += page["text"] + "\n"

    # =====================================
    # Account ID
    # =====================================

    account_match = re.search(r"ACC-\d+", full_text)

    account_id = account_match.group() if account_match else None

    # =====================================
    # Borrower Name
    # =====================================

    borrower_name = None

    company_pattern = re.compile(
        r".+\b(JSC|LLP|Ltd|Limited)\b.*",
        re.IGNORECASE,
    )

    for line in full_text.splitlines():

        line = line.strip()

        if company_pattern.fullmatch(line):
            borrower_name = line
            break

    # =====================================
    # Scenario ID
    # =====================================

    scenario_id = None

    if account_id:

        try:
            scenario_id = ledger.get_scenario(account_id)
        except ValueError:
            scenario_id = None

    # =====================================
    # Result
    # =====================================

    results.append(
        {
            "filename": doc["filename"],
            "account_id": account_id,
            "borrower_name": borrower_name,
            "scenario_id": scenario_id,
        }
    )

# =====================================
# Сортировка
# =====================================

results.sort(
    key=lambda x: (
        x["account_id"] is None,
        x["filename"],
    )
)

# =====================================
# Сохраняем
# =====================================

with open("stage2_results.json", "w", encoding="utf-8") as f:
    json.dump(
        results,
        f,
        ensure_ascii=False,
        indent=4,
    )

print("=" * 60)
print("Stage 2 completed!")
print(f"Обработано документов: {len(results)}")
print("Результат сохранён в stage2_results.json")