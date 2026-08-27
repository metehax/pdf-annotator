# 📑 PDF Annotator & Comparison Tool

An object-oriented desktop PDF annotation, manipulation, and side-by-side comparison application built with **Python**, **PySide6**, and **PyMuPDF**.

---

## 🚀 Overview

**PDF Annotator & Comparison Tool** is a desktop application designed for technical document review. It features a clean dual-pane layout, persistent multi-document sessions, an Undo/Redo architecture, and a specialized **Cross-Document Pairing Mode** for synchronizing annotations between two documents.

---

## ✨ Key Features

### 🛠️ Comprehensive Annotation Suite
* **Text Annotations:** Custom font sizes and baseline positioning.
* **Ink & Pen Tool:** Smooth freehand drawing with configurable stroke width and opacity.
* **Shapes & Connectors:** Rectangles, ovals, and arrows with 45° angle snapping (`Shift`).
* **Smart Highlighter:** Automatic text baseline snapping with transparency control.
* **True Redaction:** Permanently removes underlying text and vector graphics upon export.
* **Auto-Numbering:** Mathematically generates distinct HSV colors for sequence badges without color collisions.

### ⚏ Dual-Pane Comparison & Pairing Mode
* View and inspect two separate documents or pages side-by-side.
* **Synchronized Pairing Mode:** Place an auto-number tag on Document A, and the application automatically prompts to place the identical number and color on Document B.

### 🗂️ Advanced Document & Page Management
* **Multi-Document Tabs:** Work on several PDFs simultaneously with independent session caching and history stacks.
* **PDF Operations:** Merge multiple PDFs, split documents, extract page ranges, or reorder pages via thumbnail drag and context menu.
* **High-Res Region Snip (Crop):** Render and export selected page regions at 4x scale directly from the PDF engine.

### 🎨 Themes & Modern Architecture
* **Dark & Light Themes:** Customized UI themes with live toggling.
* **Non-Destructive Session State:** History tracking with global and per-session Undo / Redo.

---

## 🏗️ Architecture

| Layer / Component | Class | Responsibility |
| :--- | :--- | :--- |
| **Main Window** | `MainWindow` | UI layout, toolbar, menu actions, and overall session orchestration |
| **Session State** | `DocumentSession` | Active PDF document, page caching, and annotation state persistence |
| **History System** | `HistoryManager` | Global and session-scoped Undo / Redo action stack |
| **View Engine** | `PDFView` / `ComparePane` | Interactive canvas (`QGraphicsScene`) and synchronized dual-view viewer |
| **Annotations** | `Annotation` Models | Polymorphic rendering for Text, Ink, Lines, Shapes, Highlights, Redactions, Numbers |

---

## 📦 Installation & Quick Start

### Prerequisites
* Python 3.9 or higher

### Steps

1. **Clone the repository:**
   ```bash
   git clone https://github.com/metehax/pdf-annotator.git
   cd pdf-annotator
Install dependencies:

Bash
pip install -r requirements.txt
Run the application:

Bash
python pdf_annotator.py

📋 Requirements

PySide6 >= 6.0.0
PyMuPDF >= 1.22.0

📄 License
This project is licensed under the MIT License - see the LICENSE file for details.
