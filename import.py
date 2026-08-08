from pathlib import Path
import json

import pdfplumber


class PDFDocumentParser:

    def __init__(self, pdf_folder: str, output_file: str):
        self.pdf_folder = Path(pdf_folder)
        self.output_file = Path(output_file)

        self.ocr_pages = 0
        self.documents = []

    def parse_pdf(self, pdf_path: Path) -> dict:

        print(f"Processing: {pdf_path.name}")

        pages = []

        with pdfplumber.open(pdf_path) as pdf:

            print(f"Pages: {len(pdf.pages)}")

            for page in pdf.pages:

                text = page.extract_text() or ""

                tables = page.extract_tables()

                # Пока только отмечаем страницы без текста
                if not text.strip():

                    print(
                        f"[OCR] {pdf_path.name}, "
                        f"page {page.page_number}"
                    )

                    self.ocr_pages += 1
                    text = ""

                pages.append(
                    {
                        "page": page.page_number,
                        "text": text,
                        "tables": tables,
                    }
                )

        return {
            "filename": pdf_path.name,
            "pages": pages,
        }

    def parse(self) -> list[dict]:

        if not self.pdf_folder.exists():
            raise FileNotFoundError(
                f"PDF folder not found: {self.pdf_folder}"
            )

        pdf_files = sorted(
            self.pdf_folder.glob("*.pdf")
        )

        for pdf_file in pdf_files:
            document = self.parse_pdf(pdf_file)
            self.documents.append(document)

        self._save()

        return self.documents

    def _save(self) -> None:

        with self.output_file.open(
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                self.documents,
                file,
                ensure_ascii=False,
                indent=4,
            )

        print("=" * 50)
        print("Done!")
        print(f"Processed PDFs: {len(self.documents)}")
        print(f"OCR pages: {self.ocr_pages}")
        print(f"Saved to: {self.output_file}")


if __name__ == "__main__":

    parser = PDFDocumentParser(
        pdf_folder="documents",
        output_file="parsed_documents.json",
    )

    parser.parse()