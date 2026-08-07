from pathlib import Path
import pdfplumber
import json

# Папка с PDF
pdf_folder = Path("documents")

documents = []

# Проходим по всем PDF
for pdf_file in pdf_folder.glob("*.pdf"):
    print(f"Обрабатываю: {pdf_file.name}")

    pages = []

    with pdfplumber.open(pdf_file) as pdf:
        print(f"Страниц: {len(pdf.pages)}")

        for page in pdf.pages:

            # Извлекаем текст
            text = page.extract_text()

            # Проверяем, нужен ли OCR
            if text is None or text.strip() == "":
                print(f"[OCR] {pdf_file.name}, страница {page.page_number} - нужен OCR")
                text = ""

            # Извлекаем таблицы
            tables = page.extract_tables()

            pages.append({
                "page": page.page_number,
                "text": text,
                "tables": tables
            })

    documents.append({
        "filename": pdf_file.name,
        "pages": pages
    })

# Сохраняем результат
with open("parsed_documents.json", "w", encoding="utf-8") as f:
    json.dump(documents, f, ensure_ascii=False, indent=4)

print("=" * 50)
print("Готово!")
print(f"Обработано PDF: {len(documents)}")
print("Результат сохранен в parsed_documents.json")