"""Stage 1: extract page text/tables and run optional OCR for image pages."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pdfplumber


class PDFDocumentParser:
    def __init__(self, pdf_folder: str | Path, output_file: str | Path, *, ocr_lang: str = "eng+rus"):
        self.pdf_folder = Path(pdf_folder)
        self.output_file = Path(output_file)
        self.ocr_lang = ocr_lang
        self.ocr_pages = 0
        self.ocr_succeeded = 0
        self.ocr_unavailable = 0
        self.documents: list[dict] = []

    def _ocr_page(self, pdf_path: Path, page_number: int) -> tuple[str, str | None]:
        """OCR one page when Poppler and Tesseract are available."""

        pdftoppm = shutil.which("pdftoppm")
        tesseract = shutil.which("tesseract")
        if not pdftoppm or not tesseract:
            return "", "ocr_tools_unavailable"
        try:
            with tempfile.TemporaryDirectory(prefix="halyk_ocr_") as temporary:
                prefix = Path(temporary) / "page"
                subprocess.run(
                    [
                        pdftoppm,
                        "-f",
                        str(page_number),
                        "-l",
                        str(page_number),
                        "-r",
                        "200",
                        "-png",
                        "-singlefile",
                        str(pdf_path),
                        str(prefix),
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                image = prefix.with_suffix(".png")
                result = subprocess.run(
                    [tesseract, str(image), "stdout", "-l", self.ocr_lang],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                text = result.stdout.strip()
                return (text, None if text else "ocr_empty")
        except (OSError, subprocess.CalledProcessError) as exc:
            return "", f"ocr_failed: {exc}"

    def parse_pdf(self, pdf_path: Path) -> dict:
        print(f"Processing: {pdf_path.name}")
        pages = []
        with pdfplumber.open(pdf_path) as pdf:
            print(f"Pages: {len(pdf.pages)}")
            for page in pdf.pages:
                text = (page.extract_text() or "").strip()
                text_source = "pdf_text" if text else "none"
                ocr_status = None
                if not text:
                    self.ocr_pages += 1
                    text, ocr_status = self._ocr_page(pdf_path, page.page_number)
                    if text:
                        self.ocr_succeeded += 1
                        text_source = "ocr"
                    else:
                        self.ocr_unavailable += 1
                        text_source = "unavailable"
                pages.append(
                    {
                        "page": page.page_number,
                        "text": text,
                        "tables": page.extract_tables() or [],
                        "text_source": text_source,
                        "ocr_status": ocr_status,
                    }
                )
        return {"filename": pdf_path.name, "pages": pages}

    def parse(self) -> list[dict]:
        if not self.pdf_folder.exists():
            raise FileNotFoundError(f"PDF folder not found: {self.pdf_folder}")
        for pdf_file in sorted(self.pdf_folder.glob("*.pdf")):
            self.documents.append(self.parse_pdf(pdf_file))
        self._save()
        return self.documents

    def _save(self) -> None:
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        self.output_file.write_text(
            json.dumps(self.documents, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("=" * 50)
        print("Done!")
        print(f"Processed PDFs: {len(self.documents)}")
        print(f"OCR pages: {self.ocr_pages}; succeeded: {self.ocr_succeeded}; unavailable/empty: {self.ocr_unavailable}")
        print(f"Saved to: {self.output_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract PDF text/tables with optional OCR")
    parser.add_argument("--input", type=Path, default=Path("documents"))
    parser.add_argument("--output", type=Path, default=Path("parsed_documents.json"))
    parser.add_argument("--ocr-lang", default="eng+rus")
    args = parser.parse_args()
    PDFDocumentParser(args.input, args.output, ocr_lang=args.ocr_lang).parse()


if __name__ == "__main__":
    main()
