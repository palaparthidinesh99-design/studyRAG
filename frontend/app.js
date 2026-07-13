// ==========================================================================
// STUDYRAG CLIENT ENGINE (JAVASCRIPT STATE & API ROUTER)
// ==========================================================================

const BASE_URL = "https://studyrag-3s4g.onrender.com";

// Application State
let state = {
    token: localStorage.getItem("token") || null,
    userEmail: localStorage.getItem("userEmail") || null,
    authMode: "login", // "login" | "register"
    subjects: [],
    activeSubjectId: null,
    globalBooks: [],
    linkedBookIds: [], // Linked book titles
    personalSources: [],
    activeTab: "chat", // "chat" | "sources"
    ocrFile: null
};

// ==========================================================================
// INITIALIZATION
// ==========================================================================

document.addEventListener("DOMContentLoaded", () => {
    if (state.token) {
        showDashboard();
    } else {
        showAuth();
    }
});

// Switch between Login and Sign Up screens
function switchAuthTab(mode) {
    state.authMode = mode;
    const tabs = document.querySelectorAll(".auth-tab");
    tabs[0].classList.toggle("active", mode === "login");
    tabs[1].classList.toggle("active", mode === "register");
    
    // Clear alerts
    document.getElementById("auth-error").classList.add("hidden");
    document.getElementById("auth-success").classList.add("hidden");
}

// Show/Hide page sections
function showAuth() {
    document.getElementById("auth-layer").classList.remove("hidden");
    document.getElementById("dashboard-layer").classList.add("hidden");
    switchAuthTab("login");
}

function showDashboard() {
    document.getElementById("auth-layer").classList.add("hidden");
    document.getElementById("dashboard-layer").classList.remove("hidden");
    document.getElementById("user-email-display").textContent = state.userEmail;
    
    loadDashboardData();
}

// ==========================================================================
// AUTHENTICATION HANDLERS
// ==========================================================================

async function handleAuthSubmit(event) {
    event.preventDefault();
    
    const email = document.getElementById("auth-email").value.trim();
    const password = document.getElementById("auth-password").value;
    const errorAlert = document.getElementById("auth-error");
    const successAlert = document.getElementById("auth-success");
    const submitBtn = document.getElementById("auth-submit-btn");
    
    errorAlert.classList.add("hidden");
    successAlert.classList.add("hidden");
    submitBtn.disabled = true;
    submitBtn.querySelector("span").textContent = "Processing...";
    
    try {
        const endpoint = state.authMode === "login" ? "/login" : "/register";
        const response = await fetch(`${BASE_URL}${endpoint}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email, password })
        });
        
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.detail || "Authentication failed");
        }
        
        if (state.authMode === "register") {
            successAlert.textContent = "Account registered successfully! Logging in...";
            successAlert.classList.remove("hidden");
            // Auto login after registration
            state.authMode = "login";
            submitBtn.disabled = false;
            submitBtn.querySelector("span").textContent = "Continue";
            handleAuthSubmit(event);
            return;
        }
        
        // Log in success
        state.token = data.access_token;
        state.userEmail = email;
        localStorage.setItem("token", state.token);
        localStorage.setItem("userEmail", state.userEmail);
        
        showDashboard();
    } catch (err) {
        errorAlert.textContent = err.message;
        errorAlert.classList.remove("hidden");
    } finally {
        submitBtn.disabled = false;
        submitBtn.querySelector("span").textContent = "Continue";
    }
}

function handleLogout() {
    state.token = null;
    state.userEmail = null;
    state.activeSubjectId = null;
    localStorage.removeItem("token");
    localStorage.removeItem("userEmail");
    showAuth();
}

// Helper to make authenticated requests
async function authFetch(url, options = {}) {
    if (!options.headers) options.headers = {};
    options.headers["Authorization"] = `Bearer ${state.token}`;
    
    try {
        const res = await fetch(url, options);
        if (res.status === 401) {
            handleLogout();
            throw new Error("Session expired. Please log in again.");
        }
        return res;
    } catch (err) {
        console.error("API error:", err);
        throw err;
    }
}

// ==========================================================================
// DASHBOARD LOGIC (SUBJECTS & CATALOGUES)
// ==========================================================================

async function loadDashboardData() {
    try {
        await loadSubjects();
    } catch (err) {
        console.error("Failed to load initial data", err);
    }
}

async function loadSubjects() {
    try {
        const res = await authFetch(`${BASE_URL}/subjects`);
        state.subjects = await res.json();
        renderSubjects();
    } catch (err) {
        alert("Failed to load subjects list");
    }
}

function renderSubjects() {
    const list = document.getElementById("subjects-list");
    list.innerHTML = "";
    
    if (state.subjects.length === 0) {
        list.innerHTML = `<li class="history-placeholder">No subjects created yet.</li>`;
        return;
    }
    
    state.subjects.forEach(subject => {
        const li = document.createElement("li");
        li.className = `nav-item ${state.activeSubjectId === subject.id ? "active" : ""}`;
        li.onclick = () => selectSubject(subject.id);
        
        li.innerHTML = `
            <div class="nav-item-meta">
                <i class="fa-solid fa-graduation-cap"></i>
                <span>${escapeHTML(subject.name)}</span>
            </div>
        `;
        list.appendChild(li);
    });
}

// Modal popups logic
function openSubjectModal() {
    document.getElementById("subject-modal").classList.remove("hidden");
    document.getElementById("new-subject-name").focus();
}

function closeSubjectModal() {
    document.getElementById("subject-modal").classList.add("hidden");
    document.getElementById("create-subject-form").reset();
}

async function handleCreateSubject(event) {
    event.preventDefault();
    const name = document.getElementById("new-subject-name").value.trim();
    if (!name) return;
    
    try {
        const res = await authFetch(`${BASE_URL}/subjects`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name })
        });
        
        if (!res.ok) throw new Error("Failed to create subject");
        
        const newSubject = await res.json();
        closeSubjectModal();
        
        // Reload list and activate the new subject
        await loadSubjects();
        selectSubject(newSubject.id);
    } catch (err) {
        alert(err.message);
    }
}

async function selectSubject(subjectId) {
    state.activeSubjectId = subjectId;
    renderSubjects();
    
    document.getElementById("subject-empty-view").classList.add("hidden");
    document.getElementById("subject-active-view").classList.remove("hidden");
    
    const subject = state.subjects.find(s => s.id === subjectId);
    document.getElementById("active-subject-name").textContent = subject ? subject.name : "Subject";
    
    // Clear search bar and global books catalog placeholder
    document.getElementById("openstax-search-input").value = "";
    document.getElementById("global-books-list").innerHTML = `
        <li class="sources-placeholder">Type in the search bar above to search all books in the OpenStax catalog...</li>
    `;
    
    // Clear chat console
    resetChatFeed();
    
    // Refresh stats & resources
    await refreshSubjectData();
    switchMainTab("chat");
}

async function refreshSubjectData() {
    if (!state.activeSubjectId) return;
    
    try {
        await Promise.all([
            loadLinkedBooks(),
            loadPersonalSources(),
            loadQueryHistory()
        ]);
        
        // Update Stats Counters
        const ragCount = state.personalSources.filter(s => s.source_type !== "generated_note").length;
        document.getElementById("stat-sources").innerHTML = `<i class="fa-solid fa-file-pdf"></i> ${ragCount} Sources Uploaded`;
        document.getElementById("stat-books").innerHTML = `<i class="fa-solid fa-book"></i> ${state.linkedBookIds.length} Books Linked`;
        
        // Render lists
        renderPersonalSources();
        if (state.globalBooks.length > 0) {
            renderGlobalBooks();
        }
    } catch (err) {
        console.error("Error refreshing active subject details:", err);
    }
}

// Tab Selection (Chat / Materials)
function switchMainTab(tab) {
    state.activeTab = tab;
    
    // Toggle active buttons
    document.getElementById("tab-btn-chat").classList.toggle("active", tab === "chat");
    document.getElementById("tab-btn-sources").classList.toggle("active", tab === "sources");
    
    // Toggle panels
    document.getElementById("panel-chat").classList.toggle("active", tab === "chat");
    document.getElementById("panel-sources").classList.toggle("active", tab === "sources");
}

// ==========================================================================
// STUDY MATERIALS: PDF/IMAGE UPLOADS & GLOBAL BOOKS LINKING
// ==========================================================================

async function loadLinkedBooks() {
    try {
        const res = await authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/books`);
        if (!res.ok) throw new Error("Failed to load linked books");
        const data = await res.json();
        state.linkedBookIds = Array.isArray(data) ? data : [];
    } catch (err) {
        console.error("Failed to load linked books list", err);
        state.linkedBookIds = [];
    }
}

// OpenStax search keypress
function handleOpenStaxSearchKeyup(event) {
    if (event.key === "Enter") {
        triggerOpenStaxSearch();
    }
}

async function triggerOpenStaxSearch() {
    const query = document.getElementById("openstax-search-input").value.trim();
    if (!query) return;
    
    const list = document.getElementById("global-books-list");
    list.innerHTML = `<li class="sources-placeholder"><i class="fa-solid fa-spinner fa-spin"></i> Searching OpenStax & arXiv global catalogue...</li>`;
    
    try {
        const res = await fetch(`${BASE_URL}/catalogue/search?query=${encodeURIComponent(query)}`);
        if (!res.ok) throw new Error("Failed to search global catalog");
        
        state.globalBooks = await res.json();
        renderGlobalBooks();
    } catch (err) {
        list.innerHTML = `<li class="sources-placeholder error-text">⚠️ Error: ${err.message}</li>`;
    }
}

function renderGlobalBooks() {
    const list = document.getElementById("global-books-list");
    list.innerHTML = "";
    
    if (state.globalBooks.length === 0) {
        list.innerHTML = `<li class="sources-placeholder">No matching textbooks or research papers found.</li>`;
        return;
    }
    
    state.globalBooks.forEach(book => {
        const li = document.createElement("li");
        li.className = "book-card";
        
        const isLinked = state.linkedBookIds.some(title => title.toLowerCase() === book.title.toLowerCase());
        const buttonHTML = isLinked 
            ? `<button class="btn-link linked" disabled>Linked ✓</button>`
            : `<button class="btn-link" onclick="linkOpenStaxBook(this, '${escapeHTML(book.source_id)}', '${escapeHTML(book.title)}', '${escapeHTML(book.pdf_url)}', '${escapeHTML(book.source)}')">Link Material</button>`;
        
        const coverImg = book.cover_url 
            ? `<img src="${book.cover_url}" style="width: 48px; height: 64px; border-radius: 4px; object-fit: cover; border: 1px solid var(--border-color); flex-shrink:0;">`
            : `<div style="width: 48px; height: 64px; border-radius: 4px; background: rgba(255,255,255,0.05); display:flex; align-items:center; justify-content:center; flex-shrink:0;"><i class="fa-solid ${book.source === 'arxiv' ? 'fa-file-pdf' : 'fa-book'}" style="color:var(--text-muted);"></i></div>`;
            
        const sourceLabel = book.source === 'arxiv' ? 'arXiv Engineering Paper' : 'OpenStax College Catalog';
        const sourceBadgeClass = book.source === 'arxiv' ? 'badge-arxiv' : 'badge-openstax';
            
        li.innerHTML = `
            <div style="display:flex; gap:12px; align-items:center;">
                ${coverImg}
                <div>
                    <div class="book-title" title="${escapeHTML(book.title)}">${escapeHTML(book.title)}</div>
                    <div class="book-author"><span class="source-badge ${sourceBadgeClass}">${sourceLabel}</span></div>
                </div>
            </div>
            ${buttonHTML}
        `;
        list.appendChild(li);
    });
}

async function linkOpenStaxBook(button, sourceId, title, pdfUrl, source) {
    if (!state.activeSubjectId) return;
    
    button.disabled = true;
    button.textContent = "Linking...";
    
    try {
        const res = await authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/books/global`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                source_id: sourceId,
                title: title,
                pdf_url: pdfUrl,
                source: source
            })
        });
        
        if (!res.ok) throw new Error("Could not link material");
        
        button.textContent = "Linked ✓";
        button.classList.add("linked");
        button.disabled = true;
        
        // Refresh subject details
        await refreshSubjectData();
    } catch (err) {
        alert(err.message);
        button.disabled = false;
        button.textContent = "Link Material";
    }
}

async function loadPersonalSources() {
    try {
        const res = await authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/sources`);
        state.personalSources = await res.json();
    } catch (err) {
        console.error("Failed to load personal sources", err);
    }
}

function renderPersonalSources() {
    // Render list for Part A: RAG Reference documents
    const refList = document.getElementById("ref-sources-list");
    refList.innerHTML = "";
    
    const refSources = state.personalSources.filter(s => s.source_type !== "generated_note");
    if (refSources.length === 0) {
        refList.innerHTML = `<li class="sources-placeholder">No reference sources (senior notes, PDFs, papers) uploaded yet.</li>`;
    } else {
        refSources.forEach(source => {
            const li = document.createElement("li");
            li.className = "source-item";
            li.onclick = () => openSourceReader(source);
            
            const isPdf = source.source_type === "text_pdf";
            const isSavedNote = source.source_type === "saved_note";
            
            let iconClass = "fa-solid fa-file-pdf pdf";
            let badgeText = "PDF Doc";
            
            if (isSavedNote) {
                iconClass = "fa-solid fa-bookmark note";
                badgeText = "Saved Chat";
            } else if (!isPdf) {
                iconClass = "fa-solid fa-image image";
                badgeText = "Whiteboard";
            }
            
            li.innerHTML = `
                <div class="source-item-info">
                    <i class="${iconClass}"></i>
                    <span class="source-title-text" title="${escapeHTML(source.title)}">${escapeHTML(source.title)}</span>
                </div>
                <span class="source-badge">${badgeText}</span>
            `;
            refList.appendChild(li);
        });
    }
    
    // Render list for Part B: AI Structured Study Guides
    const genList = document.getElementById("gen-sources-list");
    genList.innerHTML = "";
    
    const genSources = state.personalSources.filter(s => s.source_type === "generated_note");
    if (genSources.length === 0) {
        genList.innerHTML = `<li class="sources-placeholder">No structured notes generated yet.</li>`;
    } else {
        genSources.forEach(source => {
            const li = document.createElement("li");
            li.className = "source-item";
            li.onclick = () => openSourceReader(source);
            
            li.innerHTML = `
                <div class="source-item-info">
                    <i class="fa-solid fa-robot note"></i>
                    <span class="source-title-text" title="${escapeHTML(source.title)}">${escapeHTML(source.title)}</span>
                </div>
                <span class="source-badge">AI Notes</span>
            `;
            genList.appendChild(li);
        });
    }
}

// Drag & Drop / File selection triggers
function triggerSourceSelect(part) {
    if (part === 'ref') {
        document.getElementById("ref-file-input").click();
    } else {
        document.getElementById("gen-file-input").click();
    }
}

function handleSourceSelect(event, part) {
    const file = event.target.files[0];
    if (file) uploadStudyFile(file, part);
}

function handleDragOver(e, part) {
    e.preventDefault();
    const zoneId = part === 'ref' ? "dropzone-ref" : "dropzone-gen";
    document.getElementById(zoneId).classList.add("drag-active");
}

function handleDragLeave(part) {
    const zoneId = part === 'ref' ? "dropzone-ref" : "dropzone-gen";
    document.getElementById(zoneId).classList.remove("drag-active");
}

function handleDrop(e, part) {
    e.preventDefault();
    handleDragLeave(part);
    const file = e.dataTransfer.files[0];
    if (file) uploadStudyFile(file, part);
}

async function uploadStudyFile(file, part) {
    if (!state.activeSubjectId) return;
    
    const containerId = part === 'ref' ? "progress-ref" : "progress-gen";
    const fillId = part === 'ref' ? "fill-ref" : "fill-gen";
    const percentId = part === 'ref' ? "percent-ref" : "percent-gen";
    const statusId = part === 'ref' ? "status-ref" : "status-gen";
    const filenameId = part === 'ref' ? "filename-ref" : "filename-gen";
    
    const progressContainer = document.getElementById(containerId);
    const progressFill = document.getElementById(fillId);
    const progressPercent = document.getElementById(percentId);
    const progressText = document.getElementById(statusId);
    
    document.getElementById(filenameId).textContent = file.name;
    progressFill.style.width = "0%";
    progressPercent.textContent = "0%";
    progressText.textContent = part === 'ref' ? "Uploading to RAG vector database..." : "AI is transcribing and restructuring notes...";
    progressContainer.classList.remove("hidden");
    
    const formData = new FormData();
    formData.append("file", file);
    
    try {
        // Simulated progress logic
        let prog = 0;
        const interval = setInterval(() => {
            if (prog < 90) {
                prog += 10;
                progressFill.style.width = `${prog}%`;
                progressPercent.textContent = `${prog}%`;
            }
        }, 300);
        
        const endpoint = part === 'ref' ? `/subjects/${state.activeSubjectId}/sources` : `/subjects/${state.activeSubjectId}/generate-notes`;
        const res = await fetch(`${BASE_URL}${endpoint}`, {
            method: "POST",
            headers: {
                "Authorization": `Bearer ${state.token}`
            },
            body: formData
        });
        
        clearInterval(interval);
        
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || "Failed to process raw notes file");
        }
        
        progressFill.style.width = "100%";
        progressPercent.textContent = "100%";
        progressText.textContent = "Processing Completed Successfully!";
        
        setTimeout(() => {
            progressContainer.classList.add("hidden");
        }, 2000);
        
        // Refresh sources list
        await refreshSubjectData();
        
        // If it was structured notes, open the resulting note directly in reader!
        if (part === 'gen' && data.content) {
            openReader(data.title, data.content);
        }
    } catch (err) {
        clearInterval(interval);
        progressText.textContent = `Error: ${err.message}`;
        progressFill.style.backgroundColor = "#ef4444";
        setTimeout(() => {
            progressContainer.classList.add("hidden");
            progressFill.style.backgroundColor = "var(--primary)";
        }, 5000);
    }
}

// ==========================================================================
// SOURCE READER MODAL
// ==========================================================================

async function openSourceReader(source) {
    if (source.source_type === "text_pdf" && !source.storage_path.endsWith(".md")) {
        // Native PDFs aren't raw text displayable out of context, show details or notify
        openReader(source.title, `<p>This is a RAG context PDF reference file: <strong>${escapeHTML(source.title)}</strong>.</p><p>Its sections have been indexed and embedded inside your Chroma vector database collection. The chatbot accesses this content automatically to formulate answers.</p>`);
        return;
    }
    
    // Fetch raw markdown content from server
    try {
        const res = await authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/sources/${source.id}/content`);
        if (!res.ok) throw new Error("Failed to load text content");
        const data = await res.json();
        
        openReader(source.title, data.content);
    } catch (err) {
        alert(err.message);
    }
}

function openReader(title, markdown) {
    document.getElementById("reader-title").textContent = title;
    document.getElementById("reader-content").innerHTML = formatMarkdown(markdown);
    document.getElementById("reader-modal").classList.remove("hidden");
}

function closeReaderModal() {
    document.getElementById("reader-modal").classList.add("hidden");
    document.getElementById("reader-content").innerHTML = "";
}

function copyReaderContent() {
    const text = document.getElementById("reader-content").innerText;
    navigator.clipboard.writeText(text).then(() => {
        alert("Study notes text copied to clipboard!");
    });
}

// ==========================================================================
// CHAT & GROUNDED RAG QA LOGIC
// ==========================================================================

function resetChatFeed() {
    const feed = document.getElementById("chat-feed");
    feed.innerHTML = `
        <div class="welcome-chat-message">
            <div class="welcome-icon">
                <i class="fa-solid fa-feather-pointed"></i>
            </div>
            <h3>Ask Your Context-Grounded Question</h3>
            <p>I will search your uploaded notes and linked global textbooks to generate a formatted, cited answer.</p>
            <div class="chat-quick-tags">
                <span>Ground-truth retrieval</span>
                <span>No audio hallucination</span>
                <span>Page-level citations</span>
            </div>
        </div>
    `;
}

function triggerOcrSelect() {
    document.getElementById("ocr-file-input").click();
}

function handleOcrSelect(event) {
    const file = event.target.files[0];
    if (!file) return;
    
    state.ocrFile = file;
    document.getElementById("ocr-preview-filename").textContent = file.name;
    document.getElementById("ocr-preview-container").classList.remove("hidden");
    document.getElementById("chat-input").placeholder = "Ask a question about this whiteboard/photo note...";
}

function cancelOcrUpload() {
    state.ocrFile = null;
    document.getElementById("ocr-file-input").value = "";
    document.getElementById("ocr-preview-container").classList.add("hidden");
    document.getElementById("chat-input").placeholder = "Ask a question about this subject...";
}

// Load query history
async function loadQueryHistory() {
    try {
        const res = await authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/history`);
        const history = await res.json();
        renderQueryHistory(history);
    } catch (err) {
        console.error("Failed to load history list", err);
    }
}

function renderQueryHistory(history) {
    const list = document.getElementById("history-list");
    list.innerHTML = "";
    
    if (history.length === 0) {
        list.innerHTML = `<li class="history-placeholder">No query history yet</li>`;
        return;
    }
    
    history.forEach(item => {
        const li = document.createElement("li");
        li.className = "history-item";
        li.onclick = () => loadPastQuery(item);
        
        const isPhoto = item.input_type === "photo";
        const icon = isPhoto ? "fa-regular fa-image" : "fa-regular fa-message";
        const textPreview = item.extracted_text || "Image Query";
        
        li.innerHTML = `
            <i class="${icon}"></i>
            <span class="text-ellipsis" style="max-width: 190px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${escapeHTML(textPreview)}">
                ${escapeHTML(textPreview)}
            </span>
        `;
        list.appendChild(li);
    });
}

function loadPastQuery(item) {
    // Append user message
    appendMessage("user", item.extracted_text);
    
    // Append answer with citations
    appendMessage("assistant", item.generated_answer, item.sections_used);
}

// Send Query Handler
async function handleQuerySubmit(event) {
    event.preventDefault();
    if (!state.activeSubjectId) return;
    
    const inputElement = document.getElementById("chat-input");
    const queryText = inputElement.value.trim();
    if (!queryText && !state.ocrFile) return;
    
    // Add user message to UI
    appendMessage("user", queryText || `[Attached image: ${state.ocrFile.name}]`);
    inputElement.value = "";
    
    // Render typing placeholder indicator
    const typingBubble = appendTypingIndicator();
    
    try {
        let res;
        if (state.ocrFile) {
            // Multi-part image query
            const formData = new FormData();
            formData.append("file", state.ocrFile);
            
            // Clean state ocr preview immediately
            cancelOcrUpload();
            
            res = await fetch(`${BASE_URL}/subjects/${state.activeSubjectId}/query/photo`, {
                method: "POST",
                headers: { "Authorization": `Bearer ${state.token}` },
                body: formData
            });
        } else {
            // Text-only query
            res = await fetch(`${BASE_URL}/subjects/${state.activeSubjectId}/query/text`, {
                method: "POST",
                headers: { 
                    "Content-Type": "application/json",
                    "Authorization": `Bearer ${state.token}` 
                },
                body: JSON.stringify({ query: queryText })
            });
        }
        
        typingBubble.remove();
        
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || "Server failed to process query");
        }
        
        // Render answer message
        appendMessage("assistant", data.answer, data.sources, queryText);
        
        // Reload history
        loadQueryHistory();
    } catch (err) {
        typingBubble.remove();
        appendMessage("assistant", `⚠️ Error: ${err.message}`);
    }
}

// Append message bubbles to feed helper
function appendMessage(role, text, citations = [], originalQuery = "") {
    const feed = document.getElementById("chat-feed");
    
    // Remove welcome card if present
    const welcomeCard = feed.querySelector(".welcome-chat-message");
    if (welcomeCard) welcomeCard.remove();
    
    const bubble = document.createElement("div");
    bubble.className = `message-bubble ${role}`;
    
    const avatarIcon = role === "user" ? "fa-regular fa-user" : "fa-solid fa-microchip-ai";
    let bodyHTML = `<div>${formatMarkdown(text)}</div>`;
    
    // Build citation chips if assistant and citations exist
    if (role === "assistant" && citations && citations.length > 0) {
        bodyHTML += `
            <div class="citations-container">
                <div class="citations-header">Citations (${citations.length})</div>
                <div class="citations-list">
        `;
        
        // Create deduplicated cited sources strings
        const seen = new Set();
        citations.forEach(src => {
            let label = "";
            if (src.source_type === "global_book") {
                label = `${src.source_name} (ch.${src.section.split(" ")[0]} p.${src.page})`;
            } else {
                label = `Upload: ${src.source_name}`;
            }
            
            if (!seen.has(label)) {
                seen.add(label);
                bodyHTML += `
                    <div class="citation-chip" title="Grounded source citation">
                        <i class="fa-solid fa-link"></i>
                        <span>${escapeHTML(label)}</span>
                    </div>
                `;
            }
        });
        
        bodyHTML += `
                </div>
            </div>
        `;
    }
    
    // If assistant, provide a "Save as Study Note" CTA footer!
    if (role === "assistant" && text && !text.startsWith("⚠️ Error:") && !text.startsWith("Hi") && !text.startsWith("Hello")) {
        // Deduce title
        const cleanQuery = originalQuery.replace(/[^a-zA-Z0-9 ]/g, "").substring(0, 30);
        const titleStr = cleanQuery ? `QA - ${cleanQuery}` : "Saved Study Note";
        
        bodyHTML += `
            <div class="chat-action-footer">
                <button class="btn-save-note" onclick="saveMessageAsNote(this, '${escapeHTML(titleStr)}', ${JSON.stringify(text).replace(/'/g, "\\'")})">
                    <i class="fa-regular fa-bookmark"></i> Save as Study Note
                </button>
            </div>
        `;
    }
    
    bubble.innerHTML = `
        <div class="message-avatar">
            <i class="${avatarIcon}"></i>
        </div>
        <div class="message-body">
            ${bodyHTML}
        </div>
    `;
    
    feed.appendChild(bubble);
    
    // Smooth scroll down
    feed.scrollTo({
        top: feed.scrollHeight,
        behavior: 'smooth'
    });
}

async function saveMessageAsNote(button, title, content) {
    if (!state.activeSubjectId) return;
    
    button.disabled = true;
    button.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Saving...`;
    
    try {
        const res = await authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/saved-notes`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title, content })
        });
        
        if (!res.ok) throw new Error("Failed to save note");
        
        button.innerHTML = `<i class="fa-solid fa-circle-check"></i> Saved Note ✓`;
        button.className = "btn-save-note saved";
        button.disabled = true;
        
        // Refresh sources lists in materials
        await refreshSubjectData();
    } catch (err) {
        alert(err.message);
        button.disabled = false;
        button.innerHTML = `<i class="fa-regular fa-bookmark"></i> Save as Study Note`;
    }
}

function appendTypingIndicator() {
    const feed = document.getElementById("chat-feed");
    const bubble = document.createElement("div");
    bubble.className = "message-bubble assistant";
    
    bubble.innerHTML = `
        <div class="message-avatar">
            <i class="fa-solid fa-microchip-ai"></i>
        </div>
        <div class="message-body">
            <div class="typing-dots">
                <span></span>
                <span></span>
                <span></span>
            </div>
        </div>
    `;
    
    feed.appendChild(bubble);
    feed.scrollTo({ top: feed.scrollHeight, behavior: 'smooth' });
    return bubble;
}

// ==========================================================================
// STRING & MARKDOWN HELPERS
// ==========================================================================

function escapeHTML(str) {
    if (!str) return "";
    return str.replace(/[&<>'"]/g, 
        tag => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[tag] || tag)
    );
}

// Markdown to HTML formatter for bolding, bullet points, headers, tables, and paragraphs
function formatMarkdown(text) {
    if (!text) return "";
    let html = escapeHTML(text);
    
    // Parse tables
    const lines = html.split("\n");
    let inTable = false;
    let tableHtml = "";
    
    for (let i = 0; i < lines.length; i++) {
        let line = lines[i].trim();
        
        if (line.startsWith("|") && line.endsWith("|")) {
            if (!inTable) {
                inTable = true;
                tableHtml = "<table>";
            }
            
            const cols = line.split("|").slice(1, -1).map(c => c.trim());
            // Ignore separators
            if (cols.every(c => c.startsWith("-"))) {
                continue;
            }
            
            const tag = tableHtml === "<table>" ? "th" : "td";
            tableHtml += "<tr>" + cols.map(c => `<${tag}>${c}</${tag}>`).join("") + "</tr>";
            lines[i] = ""; // clear processed line
        } else {
            if (inTable) {
                inTable = false;
                tableHtml += "</table>";
                lines[i] = tableHtml + "\n" + lines[i];
                tableHtml = "";
            }
        }
    }
    html = lines.join("\n");
    
    // Formats paragraphs
    html = html.split("\n\n").map(p => {
        p = p.trim();
        if (!p) return "";
        if (p.startsWith("<table") || p.startsWith("<tr>") || p.startsWith("<h2>") || p.startsWith("<h3>")) return p;
        return `<p>${p}</p>`;
    }).join("");
    
    // Bold tags
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    
    // Headers (## Header)
    html = html.replace(/##\s+(.*?)(?=<br>|<p>|$)/g, '<h2>$1</h2>');
    html = html.replace(/#\s+(.*?)(?=<br>|<p>|$)/g, '<h2>$1</h2>');
    
    // Unordered lists bullets
    html = html.replace(/^[•\-\*]\s+(.*?)$/gm, '<li>$1</li>');
    
    // Wrap consecutive list items in <ul> tags
    html = html.replace(/(<li>.*?<\/li>)+/g, match => `<ul>${match}</ul>`);
    
    return html;
}
