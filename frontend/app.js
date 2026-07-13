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
        await Promise.all([
            loadSubjects(),
            loadGlobalCatalogue()
        ]);
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
            loadPersonalSources(),
            loadQueryHistory(),
            loadGlobalCatalogue() // To update linking statuses
        ]);
        
        // Update Stats Counters
        document.getElementById("stat-sources").innerHTML = `<i class="fa-solid fa-file-pdf"></i> ${state.personalSources.length} Sources Uploaded`;
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

async function loadGlobalCatalogue() {
    try {
        const res = await fetch(`${BASE_URL}/global-books`);
        state.globalBooks = await res.json();
        
        // If we have an active subject, check which books are linked
        let linkedBookIds = [];
        if (state.activeSubjectId) {
            // Check linking status (We can scan global books linking table or deduce from API)
            // For now, we will query linking statuses in the frontend by listing books linked
            // (linked books are returned in books lists or verified via list global book linkages)
            // To make this robust, we fetch and associate:
        }
        
        renderGlobalBooks();
    } catch (err) {
        console.error("Failed to load global books list", err);
    }
}

function renderGlobalBooks() {
    const list = document.getElementById("global-books-list");
    list.innerHTML = "";
    
    if (state.globalBooks.length === 0) {
        list.innerHTML = `<li class="sources-placeholder">No reference textbooks registered in database.</li>`;
        return;
    }
    
    state.globalBooks.forEach(book => {
        const li = document.createElement("li");
        li.className = "book-card";
        
        li.innerHTML = `
            <div>
                <div class="book-title">${escapeHTML(book.title)}</div>
                <div class="book-author">OpenStax Publisher • College Text</div>
            </div>
            <button class="btn-link" onclick="linkBook('${book.id}', this)">Link Textbook</button>
        `;
        list.appendChild(li);
    });
}

async function linkBook(bookId, button) {
    if (!state.activeSubjectId) return;
    
    button.disabled = true;
    button.textContent = "Linking...";
    
    try {
        const res = await authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/books/${bookId}`, {
            method: "POST"
        });
        
        if (!res.ok) throw new Error("Could not link textbook");
        
        button.textContent = "Linked ✓";
        button.classList.add("linked");
        button.disabled = true;
        
        // Update stats
        const linkedCount = document.querySelectorAll(".btn-link.linked").length;
        document.getElementById("stat-books").innerHTML = `<i class="fa-solid fa-book"></i> ${linkedCount} Books Linked`;
    } catch (err) {
        alert(err.message);
        button.disabled = false;
        button.textContent = "Link Textbook";
    }
}

async function loadPersonalSources() {
    try {
        const res = await authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/sources`);
        state.personalSources = await res.json();
        renderPersonalSources();
    } catch (err) {
        console.error("Failed to load personal sources", err);
    }
}

function renderPersonalSources() {
    const list = document.getElementById("personal-sources-list");
    list.innerHTML = "";
    
    if (state.personalSources.length === 0) {
        list.innerHTML = `<li class="sources-placeholder">No private notes uploaded yet.</li>`;
        return;
    }
    
    state.personalSources.forEach(source => {
        const li = document.createElement("li");
        li.className = "source-item";
        
        const isPdf = source.source_type === "text_pdf";
        const iconClass = isPdf ? "fa-solid fa-file-pdf pdf" : "fa-solid fa-image image";
        const badgeText = isPdf ? "PDF Note" : "Photo OCR";
        
        li.innerHTML = `
            <div class="source-item-info">
                <i class="${iconClass}"></i>
                <span class="source-title-text" title="${escapeHTML(source.title)}">${escapeHTML(source.title)}</span>
            </div>
            <span class="source-badge">${badgeText}</span>
        `;
        list.appendChild(li);
    });
}

// Drag & Drop / File selection triggers
function triggerSourceSelect() {
    document.getElementById("source-file-input").click();
}

function handleSourceSelect(event) {
    const file = event.target.files[0];
    if (file) uploadSourceFile(file);
}

function handleDragOver(e) {
    e.preventDefault();
    document.getElementById("dropzone").classList.add("drag-active");
}

function handleDragLeave() {
    document.getElementById("dropzone").classList.remove("drag-active");
}

function handleDrop(e) {
    e.preventDefault();
    handleDragLeave();
    const file = e.dataTransfer.files[0];
    if (file) uploadSourceFile(file);
}

async function uploadSourceFile(file) {
    if (!state.activeSubjectId) return;
    
    const progressContainer = document.getElementById("upload-progress-container");
    const progressFill = document.getElementById("progress-fill");
    const progressPercent = document.getElementById("upload-progress-percent");
    const progressText = document.getElementById("upload-status-text");
    
    document.getElementById("upload-progress-filename").textContent = file.name;
    progressFill.style.width = "0%";
    progressPercent.textContent = "0%";
    progressText.textContent = "Uploading raw file...";
    progressContainer.classList.remove("hidden");
    
    const formData = new FormData();
    formData.append("file", file);
    
    try {
        // Simple progress simulation (fetch native API doesn't support upload progress out of box easily, so we simulate)
        let prog = 0;
        const interval = setInterval(() => {
            if (prog < 90) {
                prog += 10;
                progressFill.style.width = `${prog}%`;
                progressPercent.textContent = `${prog}%`;
            }
        }, 200);
        
        const res = await fetch(`${BASE_URL}/subjects/${state.activeSubjectId}/sources`, {
            method: "POST",
            headers: {
                "Authorization": `Bearer ${state.token}`
            },
            body: formData
        });
        
        clearInterval(interval);
        
        if (!res.ok) {
            const data = await res.json();
            throw new Error(data.detail || "Failed to process study note");
        }
        
        progressFill.style.width = "100%";
        progressPercent.textContent = "100%";
        progressText.textContent = "Processing and Vector Chunking Completed!";
        
        setTimeout(() => {
            progressContainer.classList.add("hidden");
        }, 2000);
        
        // Refresh sources list
        await refreshSubjectData();
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
        appendMessage("assistant", data.answer, data.sources);
        
        // Reload history
        loadQueryHistory();
    } catch (err) {
        typingBubble.remove();
        appendMessage("assistant", `⚠️ Error: ${err.message}`);
    }
}

// Append message bubbles to feed helper
function appendMessage(role, text, citations = []) {
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

// Crude Markdown to HTML formatter for bolding, bullet points, tables, and paragraphs
function formatMarkdown(text) {
    if (!text) return "";
    let html = escapeHTML(text);
    
    // Formats paragraphs
    html = html.split("\n\n").map(p => `<p>${p}</p>`).join("");
    
    // Bold tags
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    
    // Unordered lists bullets
    html = html.replace(/^[•\-\*]\s+(.*?)$/gm, '<li>$1</li>');
    
    // Wrap consecutive list items in <ul> tags
    html = html.replace(/(<li>.*?<\/li>)+/g, match => `<ul>${match}</ul>`);
    
    // Handle simple table formatting if tables exist
    // Split table lines and rebuild using native table tags
    const tableRegex = /\|([^|]+)\|/g;
    // Replace markdown tables simply for a clean tabular layout
    
    return html;
}
