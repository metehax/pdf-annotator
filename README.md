PDF Annotator & Comparison Tool

An object-oriented, feature-rich PDF annotation and comparison application built with Python, PySide6, and PyMuPDF.

This tool goes beyond basic PDF viewing by offering advanced multi-document sessions, a robust undo/redo architecture, and a unique "Comparison Mode" designed for cross-referencing and synchronized numbering between two distinct PDF files.

✨ Key Features

Rich Annotation Suite: Includes Text, Freehand Ink, Lines/Arrows, Shapes (Rectangles/Ovals), Highlights, and an Auto-Numbering tool.

True Redaction: Unlike simple black overlays, the redaction tool permanently removes the underlying text and image data upon export.

Dual-Pane Comparison Mode: Open two documents side-by-side. Features a "Pairing Mode" where placing a number tag on one document prompts for the exact same number/color tag on the other, creating perfectly synced cross-references.

Advanced Session Management: Open multiple PDFs simultaneously. Each document maintains its own separate history state, page cache, and rendering context.

Robust History (Undo/Redo): A custom global and session-based HistoryManager tracks additions, deletions, and transformations (moves), ensuring safe editing even across different document tabs.

Page Management: Insert blank pages, duplicate, delete, move, or extract specific page ranges into new PDF files.

High-Resolution Cropping: Extract visual areas of the PDF directly from the internal render engine (not just a screen snip) to your clipboard or as a PNG.

Dark & Light Themes: Toggleable UI themes for comfortable reading and editing.

🏗️ Architecture & Tech Stack

Language: Python 3.x

GUI Framework: PySide6 (Qt for Python)

PDF Engine: PyMuPDF (fitz)

The application is built on solid Object-Oriented Programming (OOP) principles:

Annotation Base Class: Polymorphic model for all drawings, handling its own coordinate translation, Qt Scene creation, and PyMuPDF injection.

PDFDocument: A wrapper handling internal PyMuPDF rendering, text line extraction, and page manipulation safely.

DocumentSession: Encapsulates the entire state of an open PDF, including its specific annotations and undo/redo stack.

🚀 Installation

Clone this repository:

git clone https://github.com/<your-username>/pdf-annotator.git
cd pdf-annotator


Install the required dependencies:

pip install -r requirements.txt


Run the application:

python pdf_annotator.py


📸 Screenshots

(Replace these placeholder links with actual screenshots of your application)

Normal Editing Mode

Comparison & Pairing Mode





🛠️ Future Roadmap (Planned Improvements)

Refactor the single-file monolithic codebase into a modular structure (Models, Views, Controllers).

Add text search functionality.

Implement real-time collaborative annotation.

📄 License

This project is licensed under the MIT License - see the LICENSE file for details.
