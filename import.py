from pathlib import Path
import pdfplumber

pdf_folder = Path("documents")

documents = []

for pdf_file in pdf_folder.glob("*.pdf"):
    print(f"Обрабатываю: {pdf_file.name}")

    pages = []

    with pdfplumber.open(pdf_file) as pdf:
        print(f"Страниц: {len(pdf.pages)}")

        for page in pdf.pages:
            text = page.extract_text()

            pages.append({
                "page": page.page_number,
                "text": text
            })

    documents.append({
        "filename": pdf_file.name,
        "pages": pages
    })

print("Готово!")