# Walkthrough — StudyRAG Horizontal Scrolling Feeds, PDF Note Uploads & Auto-Healing PDFs

This document summarizes the changes made to StudyRAG to implement the divided resources layout, horizontally scrolling reference strips (like YouTube feeds), inline PDF/Image note guide parser options, and dynamic auto-healing book PDF fetching.

## Key Upgrades

### 1. Horizontal Scrolling Feeds (Like a YouTube Feed)
- Replaced the list grids in the **Add resources** tab with horizontally scrollable content sliders:
  - **PDF Reference Documents Column**: Now scrolls horizontally. Lists files in a clean, visual card with dynamic color spine gradients and document file badges.
  - **Scanned Notes & Images Column**: Now scrolls horizontally. Lists note images in neat cards.
  - **Linked Textbooks Column**: Now scrolls horizontally. Displays textbook titles alongside cover graphics.
- Defined `.horizontal-scroller` and `.library-item-card` elements in CSS to ensure uniform card sizes and scroll alignments.

### 2. Neat Inline PDF & Image Notes Generator
- **PDF Upload Support**: The note guide generator now accepts both PDF documents and images as source notes files.
- **Single Access Button**: Hidden the raw, default browser file input elements. Replaced them with a single clean styled button (`Select PDF or Image of Notes...`) that adapts its display icon and text description when a file is selected.
- **Exception Fix**: Resolved a JavaScript null-reference exception when fetching `badge-uploads-count` (which was causing list rendering steps to abort prematurely and showing `0 notes`). All notes are now listed correctly.

### 3. Auto-Healing Book PDF Fetching
- Resolved the `PDF Not Found` bug when loading books or clicking citations in the chat.
- Modified the `/file` request endpoint in `main.py` to auto-heal missing textbook files:
  - If a linked textbook's local PDF is missing, the backend dynamically queries OpenStax or Open Textbook Library's APIs to resolve the original book's download URL.
  - Auto-downloads the book from the source catalog to local storage on-the-fly and returns the PDF file stream cleanly.
