// ==========================================================================
// STUDYRAG FRONTEND APPLICATION CODE — ACADEMIC SPLIT-TABS WORKSPACE
// ==========================================================================

const state = {
    token: null,
    email: "",
    name: "",
    authMode: "login", // 'login' | 'register'
    subjects: [],
    activeSubjectId: null,
    activeTab: "notes", // 'notes' | 'resources' | 'chatbot'
    globalBooks: [],
    linkedBookIds: [], // stores linked book details [{id, title}]
    personalSources: [],
    queryHistory: [],
    activeQueryId: null,
    indexingPollTimeout: null,

    // Zoom levels for notes image panning
    imageZoom: 1.0,
    isDraggingImage: false,
    imageStartX: 0,
    imageStartY: 0,
    imageScrollLeft: 0,
    imageScrollTop: 0
};

// Dynamic local vs production backend resolution
const BASE_URL = window.location.hostname.includes('studyrag') || window.location.hostname.includes('onrender.com') || window.location.hostname.includes('vercel.app')
    ? 'https://studyrag-3s4g.onrender.com'
    : `http://${window.location.hostname === 'localhost' ? '127.0.0.1' : window.location.hostname}:8000`;

// Wrapper for fetch requests to automatically inject Bearer token
async function authFetch(url, options = {}) {
    options.headers = options.headers || {};
    if (state.token) {
        options.headers["Authorization"] = `Bearer ${state.token}`;
    }
    const res = await fetch(url, options);
    if (res.status === 401) {
        handleLogout();
        throw new Error("Session expired. Please log in again.");
    }
    return res;
}

// Initial page routing and event checks on DOM ready
document.addEventListener("DOMContentLoaded", () => {
    // Configure Marked option syntax properties
    if (window.marked) {
        marked.setOptions({ gfm: true, breaks: false });
        const renderer = new marked.Renderer();

        // marked v4: code(code: string, language: string, isEscaped: boolean)
        renderer.code = function (code, language) {
            const lang = (language || '').toLowerCase().trim();
            if (lang === 'mermaid') {
                // Unescape HTML entities (like &gt; and &lt; for arrows)
                const unescapedCode = code
                    .replace(/&amp;/g, '&')
                    .replace(/&lt;/g, '<')
                    .replace(/&gt;/g, '>')
                    .replace(/&quot;/g, '"')
                    .replace(/&#39;/g, "'");
                return `<div class="mermaid">${unescapedCode}</div>`;
            }
            return `<pre class="code-block"><code class="language-${lang || 'text'}">${escapeHTML(code)}</code></pre>`;
        };

        // marked v4: table(header: string, body: string)
        renderer.table = function (header, body) {
            return `<div class="md-table-wrap"><table class="md-table"><thead>${header}</thead><tbody>${body}</tbody></table></div>`;
        };

        // marked v4: tablerow(content: string)
        renderer.tablerow = function (content) {
            return `<tr>${content}</tr>\n`;
        };

        // marked v4: tablecell(content: string, flags: { header, align })
        renderer.tablecell = function (content, flags) {
            const tag = flags && flags.header ? 'th' : 'td';
            const align = flags && flags.align ? ` style="text-align:${flags.align}"` : '';
            return `<${tag}${align}>${content}</${tag}>\n`;
        };

        marked.use({ renderer });
    }

    // Initialize Mermaid diagramming rules
    if (window.mermaid) {
        mermaid.initialize({
            startOnLoad: false,
            theme: 'dark',
            securityLevel: 'loose'
        });
    }

    // Initialize image viewport controls
    initImageViewerControls();

    checkAuth();
});

async function checkAuth() {
    const token = localStorage.getItem("token");
    if (!token) {
        showAuth();
        return;
    }
    state.token = token;
    try {
        const res = await fetch(`${BASE_URL}/me`, {
            headers: {
                "Authorization": `Bearer ${token}`
            }
        });
        if (!res.ok) throw new Error("Unauthorized");
        const userData = await res.json();
        state.email = userData.email;
        state.name = userData.name || "";
        showDashboard();
    } catch (err) {
        console.error("Auto login failed:", err);
        handleLogout();
    }
}

// ==========================================================================
// AUTHENTICATION LOGIC
// ==========================================================================

function showAuth() {
    document.getElementById("auth-layer").classList.remove("hidden");
    document.getElementById("dashboard-layer").classList.add("hidden");
}

function showDashboard() {
    document.getElementById("auth-layer").classList.add("hidden");
    document.getElementById("dashboard-layer").classList.remove("hidden");
    document.getElementById("user-email-display-nav").textContent = state.email || "student@studyrag.com";
    
    const displayName = state.name || "Student";
    document.getElementById("user-name-display-nav").textContent = displayName;
    
    const initial = displayName.trim().charAt(0).toUpperCase() || "S";
    document.getElementById("user-avatar-badge").textContent = initial;

    // Hide both views until subjects have loaded to prevent a flash of empty state
    document.getElementById("subject-empty-view").classList.add("hidden");
    document.getElementById("subject-active-view").classList.add("hidden");

    loadSubjects();
}

function switchAuthMode(mode) {
    state.authMode = mode;
    const tabs = document.querySelectorAll(".auth-tab-btn");
    tabs[0].classList.toggle("active", mode === "login");
    tabs[1].classList.toggle("active", mode === "register");

    const nameGroup = document.getElementById("auth-name-group");
    const nameInput = document.getElementById("auth-name");
    if (mode === "register") {
        nameGroup.classList.remove("hidden");
        nameInput.required = true;
    } else {
        nameGroup.classList.add("hidden");
        nameInput.required = false;
        nameInput.value = "";
    }
}

async function handleAuthSubmit(event) {
    event.preventDefault();
    const name = document.getElementById("auth-name").value.trim();
    const email = document.getElementById("auth-email").value.trim();
    const password = document.getElementById("auth-password").value;
    const submitBtn = document.getElementById("auth-submit-btn");

    submitBtn.disabled = true;
    submitBtn.textContent = "Loading...";
    const endpoint = state.authMode === "register" ? "/register" : "/login";

    try {
        const payload = { email, password };
        if (state.authMode === "register") {
            payload.name = name;
        }

        const res = await fetch(`${BASE_URL}${endpoint}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Authentication failed");

        if (state.authMode === "register") {
            showToast("Registration successful! Logging you in...", "success");
            const loginRes = await fetch(`${BASE_URL}/login`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email, password })
            });
            const loginData = await loginRes.json();
            if (!loginRes.ok) throw new Error(loginData.detail || "Automatic login failed");
            state.token = loginData.access_token;
        } else {
            state.token = data.access_token;
        }

        // Fetch profile to get name
        try {
            const profileRes = await fetch(`${BASE_URL}/me`, {
                headers: { "Authorization": `Bearer ${state.token}` }
            });
            if (profileRes.ok) {
                const profileData = await profileRes.json();
                state.email = profileData.email;
                state.name = profileData.name || "";
            } else {
                state.email = email;
                state.name = "";
            }
        } catch (profileErr) {
            state.email = email;
            state.name = "";
        }

        localStorage.setItem("token", state.token);
        localStorage.setItem("user_email", state.email);
        localStorage.setItem("user_name", state.name);
        showDashboard();
    } catch (err) {
        alert(err.message);
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = "Continue";
    }
}

function handleLogout() {
    state.token = null;
    state.email = "";
    state.activeSubjectId = null;
    localStorage.removeItem("token");
    localStorage.removeItem("user_email");
    window.location.reload();
}

// ==========================================================================
// WORKSPACE SUBJECT LOGIC
// ==========================================================================

async function loadSubjects() {
    try {
        const res = await authFetch(`${BASE_URL}/subjects`);
        state.subjects = await res.json();
        renderSubjects();

        if (state.subjects.length > 0 && !state.activeSubjectId) {
            selectSubject(state.subjects[0].id);
        } else if (state.subjects.length === 0) {
            document.getElementById("subject-active-view").classList.add("hidden");
            document.getElementById("subject-empty-view").classList.remove("hidden");
        }
    } catch (err) {
        console.error("Failed to load subjects:", err);
    }
}

function renderSubjects() {
    const list = document.getElementById("subjects-list");
    list.innerHTML = "";

    if (state.subjects.length === 0) {
        list.innerHTML = `<li class="placeholder-item" style="color:var(--text-sidebar-muted);padding:8px 16px;">No subjects created</li>`;
        return;
    }

    state.subjects.forEach(subj => {
        const li = document.createElement("li");
        li.className = subj.id === state.activeSubjectId ? "subject-item active" : "subject-item";
        li.style.display = "flex";
        li.style.justifyContent = "space-between";
        li.style.alignItems = "center";

        const nameSpan = document.createElement("span");
        nameSpan.textContent = subj.name;
        nameSpan.style.flex = "1";
        nameSpan.style.cursor = "pointer";
        nameSpan.onclick = () => selectSubject(subj.id);

        const delBtn = document.createElement("button");
        delBtn.className = "btn-delete-subject";
        delBtn.innerHTML = `<i class="fa-solid fa-trash-can"></i>`;
        delBtn.style.background = "none";
        delBtn.style.border = "none";
        delBtn.style.color = "var(--text-sidebar-muted)";
        delBtn.style.cursor = "pointer";
        delBtn.style.padding = "4px 8px";
        delBtn.style.borderRadius = "4px";
        delBtn.style.fontSize = "0.85rem";
        delBtn.style.transition = "color 0.2s, background-color 0.2s";

        delBtn.onmouseover = () => {
            delBtn.style.color = "#ff4d4d";
            delBtn.style.backgroundColor = "rgba(255, 77, 77, 0.15)";
        };
        delBtn.onmouseout = () => {
            delBtn.style.color = "var(--text-sidebar-muted)";
            delBtn.style.backgroundColor = "transparent";
        };

        delBtn.onclick = async (e) => {
            e.stopPropagation();
            const confirmed = await customConfirm(`Are you sure you want to delete the subject "${subj.name}"? This will delete all its notes, uploads, and history.`);
            if (confirmed) {
                deleteSubject(subj.id);
            }
        };

        li.appendChild(nameSpan);
        li.appendChild(delBtn);
        list.appendChild(li);
    });
}

async function deleteSubject(subjId) {
    const previousSubjects = [...state.subjects];
    const previousActiveId = state.activeSubjectId;

    // Optimistic UI update
    state.subjects = state.subjects.filter(s => s.id !== subjId);
    if (state.activeSubjectId === subjId) {
        if (state.subjects.length > 0) {
            selectSubject(state.subjects[0].id);
        } else {
            state.activeSubjectId = null;
            document.getElementById("subject-active-view").classList.add("hidden");
            document.getElementById("subject-empty-view").classList.remove("hidden");
        }
    }
    renderSubjects();

    try {
        const res = await authFetch(`${BASE_URL}/subjects/${subjId}`, {
            method: "DELETE"
        });
        if (!res.ok) {
            // Rollback
            state.subjects = previousSubjects;
            state.activeSubjectId = previousActiveId;
            renderSubjects();
            const err = await res.json();
            alert(`Failed to delete subject: ${err.detail || 'Unknown error'}`);
        }
    } catch (e) {
        // Rollback
        state.subjects = previousSubjects;
        state.activeSubjectId = previousActiveId;
        renderSubjects();
        console.error("Delete subject error:", e);
        alert("Failed to delete subject. Please check connection.");
    }
}

async function selectSubject(subjId) {
    const prevQueryId = state.activeQueryId;
    const prevSubjectId = state.activeSubjectId;

    state.activeSubjectId = subjId;
    state.activeQueryId = null;

    document.getElementById("subject-empty-view").classList.add("hidden");
    document.getElementById("subject-active-view").classList.remove("hidden");

    renderSubjects();
    closeSourceViewer();
    resetChatFeed();
    switchWorkspaceTab("notes");

    // Optimistically show loading placeholders in lists
    document.getElementById("study-notes-list").innerHTML = `<li class="placeholder-item" style="grid-column:1/-1;text-align:center;padding:40px;color:var(--text-workspace-muted)"><i class="fa-solid fa-spinner fa-spin"></i> Loading notes...</li>`;
    const textbookList = document.getElementById("linked-textbooks-list");
    if (textbookList) textbookList.innerHTML = `<li class="placeholder-item" style="padding:20px;color:var(--text-workspace-muted)"><i class="fa-solid fa-spinner fa-spin"></i> Loading...</li>`;
    const pdfList = document.getElementById("personal-uploads-pdf-list");
    if (pdfList) pdfList.innerHTML = `<li class="placeholder-item" style="padding:20px;color:var(--text-workspace-muted)"><i class="fa-solid fa-spinner fa-spin"></i> Loading...</li>`;
    const imageList = document.getElementById("personal-uploads-image-list");
    if (imageList) imageList.innerHTML = `<li class="placeholder-item" style="padding:20px;color:var(--text-workspace-muted)"><i class="fa-solid fa-spinner fa-spin"></i> Loading...</li>`;

    // Trigger auto-delete for insignificant chat of the previous subject in background
    if (prevQueryId && prevSubjectId) {
        const query = state.queryHistory.find(q => q.id === prevQueryId);
        if (query && isChatInsignificant(query)) {
            try {
                authFetch(`${BASE_URL}/subjects/${prevSubjectId}/history/${prevQueryId}`, {
                    method: "DELETE"
                });
            } catch (e) {}
        }
    }

    // Fetch and render data in the background without blocking the UI
    const targetSubjectId = subjId;
    refreshSubjectData(targetSubjectId).catch(err => console.error("Error refreshing subject:", err));
}

async function refreshSubjectData(subjectId = state.activeSubjectId) {
    if (!subjectId) return;

    try {
        // Use combined endpoint — 1 round trip instead of 3 (~3x faster)
        const res = await authFetch(`${BASE_URL}/subjects/${subjectId}/data`);
        if (res.ok) {
            const data = await res.json();
            state.personalSources = data.sources || [];
            state.linkedBookIds = data.books || [];
            state.queryHistory = data.history || [];
        } else {
            // Fallback: parallel individual requests
            await Promise.all([
                loadLinkedBooks(),
                loadPersonalSources(),
                loadQueryHistory()
            ]);
        }

        // Only render if the user hasn't switched subjects while we were fetching
        if (state.activeSubjectId !== subjectId) return;

        renderLibraryTextbooks();
        renderLibraryUploads();
        renderStudyNotesList();

    if (state.isViewingHistoryItem) {
        renderQueryHistory();
    } else {
        renderQueryHistorySidebarOnly();
    }

    populateChatSourceFilterDropdown();

    const hasUnreadyBook = state.linkedBookIds.some(book => book.is_ready === false);
    const hasProcessingNote = state.personalSources.some(s => s.storage_path && s.storage_path.startsWith("processing:"));
    
    if (hasUnreadyBook || hasProcessingNote) {
        if (state.indexingPollTimeout) clearTimeout(state.indexingPollTimeout);
        state.indexingPollTimeout = setTimeout(() => {
            if (state.activeSubjectId) {
                refreshSubjectData().catch(err => console.error("Index poll refresh failed:", err));
            }
        }, 3000); // 3 seconds poll frequency during active tasks
    }
} catch (err) {
    console.error("Could not refresh subject data:", err);
    const textbookList = document.getElementById("linked-textbooks-list");
    const pdfList = document.getElementById("personal-uploads-pdf-list");
    const imageList = document.getElementById("personal-uploads-image-list");
    
    if (textbookList && textbookList.innerHTML.includes("Loading...")) {
        textbookList.innerHTML = `<li class="placeholder-item" style="padding:20px;color:var(--text-workspace-muted)">Failed to load textbooks (network issue)</li>`;
    }
    if (pdfList && pdfList.innerHTML.includes("Loading...")) {
        pdfList.innerHTML = `<li class="placeholder-item" style="padding:20px;color:var(--text-workspace-muted)">Failed to load PDFs (network issue)</li>`;
    }
    if (imageList && imageList.innerHTML.includes("Loading...")) {
        imageList.innerHTML = `<li class="placeholder-item" style="padding:20px;color:var(--text-workspace-muted)">Failed to load images (network issue)</li>`;
    }
    showToast("Slow connection detected. Please try refreshing.", "warning");
}
}

function populateChatSourceFilterDropdown() {
    const selectEl = document.getElementById("chat-source-filter");
    if (!selectEl) return;

    const currentValue = selectEl.value;

    let html = `
        <option value="all">All References (Books + Notes)</option>
        <option value="books">Only Linked Textbooks</option>
        <option value="notes">Only Uploaded Notes & Photos</option>
    `;

    // Populate Books category
    if (state.linkedBookIds && state.linkedBookIds.length > 0) {
        html += `<optgroup label="Specific Textbooks">`;
        state.linkedBookIds.forEach(book => {
            html += `<option value="${book.id}">📖 ${escapeHTML(book.title)}</option>`;
        });
        html += `</optgroup>`;
    }

    // Populate Personal Uploads category (filter out generated guides/notes list and notes_input temp files)
    const HIDDEN_TYPES = new Set(["generated_note", "saved_note", "notes_input"]);
    const personalDocs = state.personalSources.filter(s => !HIDDEN_TYPES.has(s.source_type));
    if (personalDocs && personalDocs.length > 0) {
        html += `<optgroup label="Specific Notes & Docs">`;
        personalDocs.forEach(src => {
            const icon = src.source_type === "image_ocr" ? "📷" : "📄";
            html += `<option value="${src.id}">${icon} ${escapeHTML(src.title)}</option>`;
        });
        html += `</optgroup>`;
    }

    selectEl.innerHTML = html;

    // Restore previous selection if it is still valid
    if (currentValue && selectEl.querySelector(`option[value="${currentValue}"]`)) {
        selectEl.value = currentValue;
    }
}

async function refreshSidebarOnly() {
    if (!state.activeSubjectId) return;
    try {
        const res = await authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/data`);
        if (res.ok) {
            const data = await res.json();
            state.queryHistory = data.history || [];
        } else {
            await loadQueryHistory();
        }
        renderQueryHistorySidebarOnly();
    } catch (err) {
        console.error("Could not refresh sidebar:", err);
    }
}


function openSubjectModal() {
    document.getElementById("subject-modal").classList.remove("hidden");
}

function closeSubjectModal() {
    document.getElementById("subject-modal").classList.add("hidden");
    document.getElementById("new-subject-name").value = "";
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

        if (!res.ok) throw new Error("Could not create subject.");

        const newSubj = await res.json();
        closeSubjectModal();

        await loadSubjects();
        selectSubject(newSubj.id);
        showToast("Subject created successfully!", "success");
    } catch (err) {
        alert(err.message);
    }
}

// ==========================================================================
// WORKSPACE TABS TOGGLING
// ==========================================================================

function switchWorkspaceTab(tab) {
    state.activeTab = tab;

    // Toggle active header buttons
    document.querySelectorAll(".tab-btn").forEach(btn => {
        btn.classList.remove("active");
    });
    const activeBtn = document.getElementById(`tab-btn-${tab}`);
    if (activeBtn) activeBtn.classList.add("active");

    // Toggle active panels
    document.querySelectorAll(".tab-pane").forEach(pane => {
        pane.classList.remove("active");
    });
    const activePane = document.getElementById(`tab-pane-${tab}`);
    if (activePane) activePane.classList.add("active");

    if (tab === "chatbot") {
        if (state.queryHistory && state.queryHistory.length > 0) {
            selectChatHistoryItem(state.queryHistory[0].id);
        } else {
            startNewChat();
        }
    }
}

// ==========================================================================
// TAB 1: NOTES LISTING & OCR STUDY GUIDES GENERATION
// ==========================================================================

function renderStudyNotesList() {
    const list = document.getElementById("study-notes-list");
    const countLabel = document.getElementById("notes-count-label");

    list.innerHTML = "";

    const guides = state.personalSources.filter(s => s.source_type === "generated_note" || s.source_type === "saved_note");
    countLabel.textContent = `${guides.length} notes`;

    if (guides.length === 0) {
        list.innerHTML = `<li class="placeholder-item" style="grid-column:1/-1;text-align:center;padding:40px;color:var(--text-workspace-muted)">No study notes generated yet. Upload scanned notes or images above to generate study guides.</li>`;
        return;
    }

    guides.forEach(note => {
        const li = document.createElement("li");

        if (note.storage_path && note.storage_path.startsWith("processing:")) {
            li.className = "note-card-new processing-card";
            li.style.cursor = "default";
            
            const parts = note.storage_path.split(":");
            const progress = parts[1] || "0";
            const statusMsg = parts[2] || "Drafting study guide...";
            
            li.innerHTML = `
                <div class="note-card-title" style="display:flex; justify-content:space-between; align-items:center;">
                    <span>${escapeHTML(note.title)}</span>
                    <i class="fa-solid fa-spinner fa-spin" style="color:var(--orange-accent);"></i>
                </div>
                <div class="processing-progress-bar-container" style="background:rgba(255,255,255,0.08); border-radius:10px; height:8px; overflow:hidden; margin:12px 0 6px 0;">
                    <div class="processing-progress-bar" style="background:linear-gradient(90deg, var(--orange-accent), #f97316); width:${progress}%; height:100%; transition:width 0.5s ease;"></div>
                </div>
                <div class="note-card-meta" style="display:flex; justify-content:space-between; font-size:0.8rem; color:var(--text-workspace-muted);">
                    <span>${escapeHTML(statusMsg)}</span>
                    <span style="font-weight:600; color:var(--orange-accent);">${progress}%</span>
                </div>
            `;
        } else if (note.storage_path && note.storage_path.startsWith("failed:")) {
            li.className = "note-card-new failed-card";
            li.style.border = "1px solid rgba(239, 68, 68, 0.4)";
            li.style.background = "rgba(239, 68, 68, 0.05)";
            li.style.cursor = "default";
            
            const errorMsg = note.storage_path.substring(7);
            
            li.innerHTML = `
                <div class="note-card-title" style="display:flex; justify-content:space-between; align-items:center; color:#ef4444; font-weight:600;">
                    <span>${escapeHTML(note.title)}</span>
                    <i class="fa-solid fa-triangle-exclamation"></i>
                </div>
                <div class="note-card-meta" style="color:var(--text-workspace-muted); margin-top:8px; font-size:0.8rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="Failed: ${escapeHTML(errorMsg)}">
                    <span>Error: ${escapeHTML(errorMsg)}</span>
                </div>
            `;
        } else {
            li.className = "note-card-new";
            li.onclick = () => openGuideSourceViewer(note.id, note.title);

            const createdDate = new Date(note.created_at).toLocaleDateString(undefined, {
                month: 'short',
                day: 'numeric',
                year: 'numeric'
            });

            li.innerHTML = `
                <div class="note-card-title">${escapeHTML(note.title)}</div>
                <div class="note-card-meta">
                    <i class="fa-regular fa-file-lines"></i>
                    <span>Generated Note • ${createdDate}</span>
                </div>
            `;
        }
        list.appendChild(li);
    });
}

function updateInlineOcrLabel(event) {
    const label = document.getElementById("inline-ocr-label");
    const file = event.target.files[0];
    if (file) {
        const icon = file.type === "application/pdf" ? "fa-regular fa-file-pdf" : "fa-regular fa-file-image";
        label.innerHTML = `<i class="${icon}"></i> <span>${escapeHTML(file.name)}</span>`;
    } else {
        label.innerHTML = `<i class="fa-solid fa-plus"></i> <span>Select PDF or Image of Notes...</span>`;
    }
}

let currentAnalyzeResult = null;

function openTopicsModal() {
    document.getElementById("notes-topics-modal").classList.remove("hidden");
}

function closeTopicsModal() {
    document.getElementById("notes-topics-modal").classList.add("hidden");
    document.getElementById("custom-topic-input").value = "";
    currentAnalyzeResult = null;
}

function addCustomTopic() {
    const input = document.getElementById("custom-topic-input");
    const val = input.value.trim();
    if (!val) return;

    const parts = val.split(",").map(s => s.trim()).filter(s => s.length > 0);
    const checklist = document.getElementById("topics-checklist");

    parts.forEach(topic => {
        const div = document.createElement("div");
        div.style.display = "flex";
        div.style.alignItems = "center";
        div.style.gap = "8px";

        const labelId = `custom-topic-${Math.random().toString(36).substr(2, 9)}`;
        div.innerHTML = `
            <input type="checkbox" id="${labelId}" value="${escapeHTML(topic)}" checked style="accent-color:var(--orange-accent); cursor:pointer;">
            <label for="${labelId}" style="color:var(--text-workspace-main); font-size:0.9rem; cursor:pointer; flex:1;">${escapeHTML(topic)} (Custom)</label>
            <button type="button" class="btn-delete-subject" onclick="this.parentElement.remove()" style="color:var(--text-workspace-muted); background:none; border:none; cursor:pointer;"><i class="fa-solid fa-xmark"></i></button>
        `;
        checklist.appendChild(div);
    });

    input.value = "";
}

async function handleInlineNotesGenerate(event) {
    event.preventDefault();
    const input = document.getElementById("inline-ocr-file-input");
    if (!input.files || input.files.length === 0) return;

    const file = input.files[0];
    const formData = new FormData();
    formData.append("file", file);

    const statusDiv = document.getElementById("notes-inline-status");
    const submitBtn = document.getElementById("btn-generate-inline-guide");

    submitBtn.disabled = true;
    submitBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Analyzing...`;
    statusDiv.className = "status-alert info";
    statusDiv.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Extracting writing and analyzing suggested topics... Please wait.`;
    statusDiv.classList.remove("hidden");

    try {
        const res = await authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/generate-notes/analyze`, {
            method: "POST",
            body: formData
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Outline analysis failed.");

        currentAnalyzeResult = data;

        const checklist = document.getElementById("topics-checklist");
        checklist.innerHTML = "";
        
        data.topics.forEach((topic, index) => {
            const div = document.createElement("div");
            div.style.display = "flex";
            div.style.alignItems = "center";
            div.style.gap = "8px";
            const id = `suggested-topic-${index}`;
            div.innerHTML = `
                <input type="checkbox" id="${id}" value="${escapeHTML(topic)}" checked style="accent-color:var(--orange-accent); cursor:pointer;">
                <label for="${id}" style="color:var(--text-workspace-main); font-size:0.9rem; cursor:pointer;">${escapeHTML(topic)}</label>
            `;
            checklist.appendChild(div);
        });

        statusDiv.classList.add("hidden");
        openTopicsModal();

    } catch (err) {
        statusDiv.className = "status-alert danger";
        statusDiv.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> Error: ${err.message}`;
    } finally {
        submitBtn.disabled = false;
        submitBtn.innerHTML = `<i class="fa-solid fa-wand-magic-sparkles"></i> Generate Guide`;
    }
}

async function submitNotesTopics(event) {
    event.preventDefault();
    if (!currentAnalyzeResult) return;

    const checklist = document.getElementById("topics-checklist");
    const checkboxes = checklist.querySelectorAll("input[type='checkbox']");
    const selectedTopics = [];
    checkboxes.forEach(cb => {
        if (cb.checked) {
            selectedTopics.push(cb.value);
        }
    });

    if (selectedTopics.length === 0) {
        alert("Please select at least one topic to explain.");
        return;
    }

    const startBtn = document.getElementById("btn-start-notes-gen");
    startBtn.disabled = true;
    startBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Triggering...`;

    try {
        const res = await authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/generate-notes/trigger`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                source_id: currentAnalyzeResult.source_id,
                topics: selectedTopics
            })
        });
        
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Failed to trigger notes generation.");

        closeTopicsModal();
        showToast("Study guide generation triggered in the background!", "success");

        document.getElementById("inline-ocr-file-input").value = "";
        document.getElementById("inline-ocr-label").innerHTML = `<i class="fa-solid fa-plus"></i> <span>Select PDF or Image of Notes...</span>`;

        await refreshSubjectData();
    } catch (err) {
        alert(err.message);
    } finally {
        startBtn.disabled = false;
        startBtn.innerHTML = `<i class="fa-solid fa-wand-magic-sparkles"></i> Generate Study Guide`;
    }
}

function handleNotesSearch(event) {
    const query = event.target.value.toLowerCase();
    const cards = document.querySelectorAll(".note-card-new");
    cards.forEach(card => {
        const title = card.querySelector(".note-card-title").textContent.toLowerCase();
        if (title.includes(query)) {
            card.classList.remove("hidden");
        } else {
            card.classList.add("hidden");
        }
    });
}

// ==========================================================================
// TAB 2: LIBRARY / TEXTBOOK CATALOG DIALOG
// ==========================================================================

async function loadLinkedBooks() {
    try {
        const res = await authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/books`);
        if (!res.ok) throw new Error("Failed to load linked books");
        state.linkedBookIds = await res.json();
    } catch (err) {
        console.error(err);
        state.linkedBookIds = [];
    }
}

function renderLibraryTextbooks() {
    const list = document.getElementById("linked-textbooks-list");
    const countBadge = document.getElementById("badge-textbooks-count");

    list.innerHTML = "";
    countBadge.textContent = state.linkedBookIds.length;

    if (state.linkedBookIds.length === 0) {
        list.innerHTML = `<li class="placeholder-item" style="padding:20px;color:var(--text-workspace-muted)">No textbooks linked</li>`;
        return;
    }

    state.linkedBookIds.forEach(book => {
        const li = document.createElement("li");
        const { title: displayTitle, author: displayAuthor } = parseBookTitle(book.title);
        const gradient = generateBookSpineCover(displayTitle);
        const isReady = book.is_ready !== false;

        if (!isReady) {
            li.className = "library-item-card indexing-state";
            li.innerHTML = `
                <div class="library-card-content" style="opacity: 0.5;">
                    <div class="library-card-spine" style="background: ${gradient}">
                        <i class="fa-solid fa-spinner fa-spin library-spine-icon"></i>
                    </div>
                    <div class="library-card-info">
                        <span class="library-card-title" title="${escapeHTML(displayTitle)}">${escapeHTML(displayTitle)}</span>
                        ${displayAuthor ? `<span class="library-card-author-mini">by ${escapeHTML(displayAuthor)}</span>` : ''}
                        <span class="library-card-subtext" style="color: var(--orange-accent); font-weight:600;"><i class="fa-solid fa-cloud-arrow-down" style="margin-right: 4px;"></i>Downloading...</span>
                    </div>
                </div>
                <div class="library-card-actions">
                    <button class="btn-card-action" disabled style="cursor: not-allowed; opacity: 0.5;" title="Please wait for indexing to complete">
                        <i class="fa-solid fa-clock"></i> Indexing...
                    </button>
                    <button class="btn-card-action delete" onclick="unlinkLibraryTextbook('${book.id}')" title="Unlink Textbook">
                        <i class="fa-regular fa-trash-can"></i>
                    </button>
                </div>
            `;
        } else {
            li.className = "library-item-card";
            li.innerHTML = `
                <div class="library-card-content">
                    <div class="library-card-spine" style="background: ${gradient}">
                        <i class="fa-regular fa-bookmark library-spine-icon"></i>
                    </div>
                    <div class="library-card-info">
                        <span class="library-card-title" title="${escapeHTML(displayTitle)}">${escapeHTML(displayTitle)}</span>
                        ${displayAuthor ? `<span class="library-card-author-mini">by ${escapeHTML(displayAuthor)}</span>` : ''}
                        <span class="library-card-subtext">Linked Textbook</span>
                    </div>
                </div>
                <div class="library-card-actions">
                    <button class="btn-card-action" onclick="openTextbookInNewTab('${book.id}')" title="View Book in New Tab">
                        <i class="fa-solid fa-arrow-up-right-from-square"></i> Open PDF
                    </button>
                    <button class="btn-card-action delete" onclick="unlinkLibraryTextbook('${book.id}')" title="Unlink Textbook">
                        <i class="fa-regular fa-trash-can"></i>
                    </button>
                </div>
            `;
        }
        list.appendChild(li);
    });
}

function openCatalogModal() {
    document.getElementById("catalog-modal").classList.remove("hidden");
}

function closeCatalogModal() {
    document.getElementById("catalog-modal").classList.add("hidden");
    document.getElementById("openstax-search-input").value = "";
    document.getElementById("global-books-list").innerHTML = `<li class="placeholder-item">Enter a topic to search OpenStax, LibreTexts or Project Gutenberg.</li>`;
}

function handleOpenStaxSearchKeyup(event) {
    if (event.key === "Enter") triggerOpenStaxSearch();
}

async function triggerOpenStaxSearch() {
    const query = document.getElementById("openstax-search-input").value.trim();
    if (!query) return;

    const list = document.getElementById("global-books-list");
    list.innerHTML = `<li class="placeholder-item"><i class="fa-solid fa-spinner fa-spin"></i> Searching catalogues...</li>`;

    try {
        const res = await authFetch(`${BASE_URL}/catalogue/search?query=${encodeURIComponent(query)}`);
        if (!res.ok) throw new Error("Search catalogue failed");
        state.globalBooks = await res.json();
        renderCatalogOverlayBooks();
    } catch (err) {
        list.innerHTML = `<li class="placeholder-item text-danger">${err.message}</li>`;
    }
}

function renderCatalogOverlayBooks() {
    const list = document.getElementById("global-books-list");
    list.innerHTML = "";

    if (state.globalBooks.length === 0) {
        list.innerHTML = `<li class="placeholder-item">No textbooks found.</li>`;
        return;
    }

    state.globalBooks.forEach(book => {
        const li = document.createElement("li");

        // Ensure title is a safe string
        // Ensure title is a safe string
        const bookTitle = (typeof book.title === "string") ? book.title : (Array.isArray(book.title) ? book.title[0] : (book.title || ""));
        const linkedBook = state.linkedBookIds.find(b => {
            if (!b.title || !bookTitle) return false;
            const { title: storedTitle } = parseBookTitle(b.title);
            return storedTitle.toLowerCase() === bookTitle.toLowerCase();
        });

        li.className = linkedBook ? "book-card-new linked" : "book-card-new";

        const buttonHTML = linkedBook
            ? `<button class="btn-link-new linked" onclick="unlinkOpenStaxBookFromOverlay(this, '${escapeHTML(linkedBook.id)}')"><i class="fa-solid fa-link-slash" style="margin-right: 6px;"></i>Unlink Book</button>`
            : `<button class="btn-link-new" onclick="linkOpenStaxBookFromOverlay(this, '${escapeHTML(book.source_id)}', '${escapeHTML(bookTitle)}', '${escapeHTML(book.pdf_url)}', '${escapeHTML(book.source)}', '${escapeHTML(book.author || '')}')"><i class="fa-solid fa-link" style="margin-right: 6px;"></i>Link Material</button>`;

        let coverHTML = "";
        if (book.cover_url) {
            coverHTML = `<img src="${book.cover_url}" class="book-card-cover-new">`;
        } else {
            const gradient = generateBookSpineCover(bookTitle);
            coverHTML = `
                <div class="book-spine-cover" style="background: ${gradient}">
                    <div class="book-spine-accent"></div>
                    <div class="book-spine-cover-title">${escapeHTML(bookTitle)}</div>
                </div>
            `;
        }

        const SOURCE_LABELS = {
            gutenberg: 'Project Gutenberg',
            libretexts: 'LibreTexts',
            opentextbooklibrary: 'Open Textbook Library',
            doab: 'DOAB',
            openstax: 'OpenStax'
        };
        const sourceLabel = SOURCE_LABELS[book.source] || 'OpenStax';
        const descText = String(book.description || "").replace(/<\/?[^>]+(>|$)/g, "");
        const truncatedDesc = descText.length > 75 ? descText.substring(0, 75) + "..." : descText;

        li.innerHTML = `
            <div>
                ${coverHTML}
                <div class="book-card-info-new">
                    <span class="source-badge">${sourceLabel}</span>
                    <h4 class="book-card-title-new" title="${escapeHTML(book.title)}">${escapeHTML(book.title)}</h4>
                    <span class="book-card-author-new">by ${escapeHTML(book.author || "Unknown")}</span>
                    <p class="book-card-desc-new">${escapeHTML(truncatedDesc || "No description available.")}</p>
                </div>
            </div>
            <div class="book-card-action-new">
                ${buttonHTML}
            </div>
        `;
        list.appendChild(li);
    });
}

function generateBookSpineCover(rawTitle) {
    // Strip encoded author from title for cover gradient generation
    const title = rawTitle.includes(' [by ') ? rawTitle.split(' [by ')[0] : rawTitle;
    let hash = 0;
    for (let i = 0; i < title.length; i++) {
        hash = title.charCodeAt(i) + ((hash << 5) - hash);
    }
    const h1 = Math.abs(hash % 360);
    const h2 = (h1 + 45) % 360;
    return `linear-gradient(135deg, hsl(${h1}, 45%, 28%) 0%, hsl(${h2}, 50%, 15%) 100%)`;
}

/** Decode a stored title like "Calculus [by Gilbert Strang]" → { title, author } */
function parseBookTitle(rawTitle) {
    if (rawTitle && rawTitle.includes(' [by ')) {
        const idx = rawTitle.indexOf(' [by ');
        return {
            title: rawTitle.substring(0, idx),
            author: rawTitle.substring(idx + 5, rawTitle.length - 1)
        };
    }
    return { title: rawTitle || '', author: '' };
}

async function linkOpenStaxBookFromOverlay(button, sourceId, title, pdfUrl, source, author) {
    button.disabled = true;
    button.textContent = "Linking...";
    const storedTitle = author ? `${title} [by ${author}]` : title;

    // Immediately show success toast that linking/indexing started in the background
    showToast(`Linking and downloading "${title}" in the background...`, "success");

    // Do NOT close the catalog modal here, let the user continue browsing

    // Execute authFetch asynchronously
    authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/books/global`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_id: sourceId, title: storedTitle, pdf_url: pdfUrl, source })
    }).then(async (res) => {
        if (!res.ok) throw new Error("Failed to link material");
        await refreshSubjectData();
        showToast(`"${title}" linked successfully!`, "success");
    }).catch(err => {
        console.error("Background linking error:", err);
        showToast(`Failed to link "${title}": ${err.message}`, "error");
    });
}

async function unlinkOpenStaxBookFromOverlay(button, globalBookId) {
    button.disabled = true;
    button.textContent = "Unlinking...";
    try {
        const res = await authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/books/${globalBookId}`, {
            method: "DELETE"
        });
        if (!res.ok) throw new Error("Could not unlink material");
        await refreshSubjectData();
        renderCatalogOverlayBooks();
    } catch (err) {
        alert(err.message);
        button.disabled = false;
        button.textContent = "Unlink Book";
    }
}

async function unlinkLibraryTextbook(globalBookId) {
    const confirmed = await customConfirm("Are you sure you want to unlink this textbook?");
    if (!confirmed) return;

    // Optimistic UI Update
    state.linkedBookIds = state.linkedBookIds.filter(b => b.id !== globalBookId);
    renderLibraryTextbooks();

    try {
        const res = await authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/books/${globalBookId}`, {
            method: "DELETE"
        });
        if (!res.ok) throw new Error("Could not unlink material");
        // Optionally refresh in background to ensure sync
        refreshSubjectData();
    } catch (err) {
        alert(err.message);
    }
}

// ==========================================================================
// TAB 2: MY UPLOADS & SOURCES LISTING (Divided Upper grids)
// ==========================================================================

async function loadPersonalSources() {
    try {
        const res = await authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/sources`);
        state.personalSources = await res.json();
    } catch (err) {
        console.error(err);
    }
}

function renderLibraryUploads() {
    const pdfList = document.getElementById("personal-uploads-pdf-list");
    const imageList = document.getElementById("personal-uploads-image-list");
    const countBadge = document.getElementById("badge-uploads-count");

    pdfList.innerHTML = "";
    imageList.innerHTML = "";

    // Filter documents vs study guides — also exclude 'notes_input' (notes upload temp files)
    const HIDDEN_TYPES = new Set(["generated_note", "saved_note", "notes_input"]);
    const docs = state.personalSources.filter(s => !HIDDEN_TYPES.has(s.source_type));
    if (countBadge) countBadge.textContent = docs.length;

    const pdfFiles = docs.filter(s => s.source_type === "text_pdf");
    const imageFiles = docs.filter(s => s.source_type === "image_ocr");

    if (pdfFiles.length === 0) {
        pdfList.innerHTML = `<li class="placeholder-item" style="padding:20px;color:var(--text-workspace-muted)">No PDF documents uploaded</li>`;
    } else {
        pdfFiles.forEach(src => {
            const li = document.createElement("li");
            li.className = "library-item-card";

            const gradient = generateBookSpineCover(src.title);

            li.innerHTML = `
                <div class="library-card-content">
                    <div class="library-card-spine" style="background: ${gradient}">
                        <i class="fa-regular fa-file-pdf library-spine-icon" style="color:var(--tag-personal)"></i>
                    </div>
                    <div class="library-card-info">
                        <span class="library-card-title" title="${escapeHTML(src.title)}">${escapeHTML(src.title)}</span>
                        <span class="library-card-subtext">PDF Document</span>
                    </div>
                </div>
                <div class="library-card-actions">
                    <button class="btn-card-action" onclick="openUploadSourceViewer('${src.source_type}', '${src.id}', '${escapeHTML(src.title)}')" title="View PDF">
                        <i class="fa-regular fa-eye"></i> Read File
                    </button>
                    <button class="btn-card-action delete" onclick="deleteLibraryUpload('${src.id}')" title="Delete file">
                        <i class="fa-regular fa-trash-can"></i>
                    </button>
                </div>
            `;
            pdfList.appendChild(li);
        });
    }

    if (imageFiles.length === 0) {
        imageList.innerHTML = `<li class="placeholder-item" style="padding:20px;color:var(--text-workspace-muted)">No note images uploaded</li>`;
    } else {
        imageFiles.forEach(src => {
            const li = document.createElement("li");
            li.className = "library-item-card";

            const gradient = generateBookSpineCover(src.title);

            li.innerHTML = `
                <div class="library-card-content">
                    <div class="library-card-spine" style="background: ${gradient}">
                        <i class="fa-regular fa-image library-spine-icon" style="color:var(--tag-note)"></i>
                    </div>
                    <div class="library-card-info">
                        <span class="library-card-title" title="${escapeHTML(src.title)}">${escapeHTML(src.title)}</span>
                        <span class="library-card-subtext">Scanned Notes</span>
                    </div>
                </div>
                <div class="library-card-actions">
                    <button class="btn-card-action" onclick="openUploadSourceViewer('${src.source_type}', '${src.id}', '${escapeHTML(src.title)}')" title="View Image">
                        <i class="fa-regular fa-eye"></i> View Photo
                    </button>
                    <button class="btn-card-action delete" onclick="deleteLibraryUpload('${src.id}')" title="Delete file">
                        <i class="fa-regular fa-trash-can"></i>
                    </button>
                </div>
            `;
            imageList.appendChild(li);
        });
    }
}

// Upload modals window operations
function openUploadPdfModal() {
    document.getElementById("upload-pdf-modal").classList.remove("hidden");
}
function closeUploadPdfModal() {
    document.getElementById("upload-pdf-modal").classList.add("hidden");
    document.getElementById("modal-pdf-input").value = "";
}

async function handlePdfModalSubmit(event) {
    event.preventDefault();
    const input = document.getElementById("modal-pdf-input");
    if (!input.files || input.files.length === 0) return;

    const file = input.files[0];
    const formData = new FormData();
    formData.append("file", file);

    // Close modal instantly to avoid locking UI
    closeUploadPdfModal();
    showToast(`Uploading ${file.name}...`, "info");

    try {
        const res = await authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/sources`, {
            method: "POST",
            body: formData
        });
        if (!res.ok) throw new Error("Upload processing failed");

        showToast(`${file.name} uploaded successfully!`, "success");
        await refreshSubjectData();
    } catch (err) {
        showToast(`Upload failed: ${err.message}`, "error");
    }
}

function openUploadImageModal() {
    document.getElementById("upload-image-modal").classList.remove("hidden");
}
function closeUploadImageModal() {
    document.getElementById("upload-image-modal").classList.add("hidden");
    document.getElementById("modal-image-input").value = "";
}

async function handleImageModalSubmit(event) {
    event.preventDefault();
    const input = document.getElementById("modal-image-input");
    if (!input.files || input.files.length === 0) return;

    const file = input.files[0];
    const formData = new FormData();
    formData.append("file", file);

    // Close modal instantly
    closeUploadImageModal();
    showToast(`Uploading ${file.name}...`, "info");

    try {
        const res = await authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/sources`, {
            method: "POST",
            body: formData
        });
        if (!res.ok) throw new Error("Image upload processing failed");

        showToast(`${file.name} uploaded successfully!`, "success");
        await refreshSubjectData();
    } catch (err) {
        showToast(`Upload failed: ${err.message}`, "error");
    }
}

async function deleteLibraryUpload(sourceId) {
    const confirmed = await customConfirm("Are you sure you want to delete this document from the library?");
    if (!confirmed) return;

    // Optimistic UI Update
    state.personalSources = state.personalSources.filter(s => s.id !== sourceId);
    renderLibraryUploads();

    try {
        const res = await authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/sources/${sourceId}`, {
            method: "DELETE"
        });
        if (!res.ok) throw new Error("Failed to delete source");
        // Optionally refresh in background
        refreshSubjectData();
    } catch (err) {
        alert(err.message);
    }
}

// ==========================================================================
// TAB 3: CHATBOT TUTOR FEED
// ==========================================================================

async function loadQueryHistory() {
    try {
        const res = await authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/history`);
        state.queryHistory = await res.json();
    } catch (err) {
        console.error(err);
    }
}

function isChatInsignificant(query) {
    return false;
}

async function deleteChatIfInsignificant(queryId) {
    return;
}

function renderQueryHistory() {
    const feed = document.getElementById("chat-feed");
    if (!feed) return;

    renderQueryHistorySidebarOnly();

    // 2. Render the active chat feed area on the right
    feed.innerHTML = "";
    if (state.activeQueryId) {
        const activeQuery = state.queryHistory.find(q => q.id === state.activeQueryId);
        if (activeQuery) {
            let questions = [];
            let answers = [];
            let citations = [];

            // Try parsing as multi-turn JSON lists
            try {
                questions = JSON.parse(activeQuery.extracted_text);
                if (!Array.isArray(questions)) throw new Error("not array");
            } catch (e) {
                questions = [activeQuery.extracted_text || ""];
            }
            try {
                answers = JSON.parse(activeQuery.generated_answer);
                if (!Array.isArray(answers)) throw new Error("not array");
            } catch (e) {
                answers = [activeQuery.generated_answer || ""];
            }
            try {
                citations = JSON.parse(JSON.stringify(activeQuery.sections_used));
                // If it's a multi-turn array, it should be an array of arrays.
                // If the first element is not an array, convert to single-turn wrapper array.
                if (!Array.isArray(citations) || (citations.length > 0 && !Array.isArray(citations[0]))) {
                    citations = [citations];
                }
            } catch (e) {
                citations = [activeQuery.sections_used || []];
            }

            // Render each turn sequentially without individual animated scroll transitions!
            for (let i = 0; i < questions.length; i++) {
                if (activeQuery.input_type === "photo" && i === 0) {
                    appendMessage("user", `[Photo Query: ${questions[i] || "Image Upload"}]`, [], "", "", false);
                } else {
                    appendMessage("user", questions[i], [], "", "", false);
                }
                appendMessage("assistant", answers[i] || "", citations[i] || [], questions[i], "", false);
            }
            
            // Smooth scroll to bottom once after rendering all historical turns
            setTimeout(() => {
                feed.scrollTo({ top: feed.scrollHeight, behavior: "smooth" });
            }, 50);
        } else {
            resetChatFeed();
        }
    } else {
        // Welcome screen for a new chat
        resetChatFeed();
    }
}

function renderQueryHistorySidebarOnly() {
    const sidebarList = document.getElementById("chatbot-history-list");
    if (!sidebarList) return;

    sidebarList.innerHTML = "";

    // 1. Prepend temporary active "New Chat" item if in new chat state
    if (!state.activeQueryId) {
        const tempLi = document.createElement("li");
        tempLi.className = "history-item active";
        tempLi.innerHTML = `<i class="fa-regular fa-message"></i><span>New Chat</span>`;
        sidebarList.appendChild(tempLi);
    }

    const recentHistory = (state.queryHistory || []).slice(0, 2);
    if (recentHistory && recentHistory.length > 0) {
        recentHistory.forEach(q => {
            const li = document.createElement("li");
            const isActive = q.id === state.activeQueryId;
            li.className = isActive ? "history-item active" : "history-item";

            // Extract first main (non-greeting) question as title text
            let rawText = "New Chat";
            if (typeof q.extracted_text === "string" && q.extracted_text.trim().startsWith("[")) {
                try {
                    const parsed = JSON.parse(q.extracted_text);
                    if (Array.isArray(parsed) && parsed.length > 0) {
                        const greetingPattern = /^(hi|hello|hey|greetings|howdy|what'?s up|how are you|thanks|thank you|good morning|good afternoon|good evening)\b/i;
                        const significantQ = parsed.find(msg => !greetingPattern.test(msg.trim()));
                        if (significantQ) {
                            rawText = significantQ;
                        }
                    }
                } catch (e) { }
            } else if (q.extracted_text) {
                const greetingPattern = /^(hi|hello|hey|greetings|howdy|what'?s up|how are you|thanks|thank you|good morning|good afternoon|good evening)\b/i;
                if (!greetingPattern.test(q.extracted_text.trim())) {
                    rawText = q.extracted_text;
                }
            }

            const titleText = rawText.length > 25 ? rawText.substring(0, 25) + "..." : rawText;
            const iconClass = q.input_type === "photo" ? "fa-regular fa-image" : "fa-regular fa-message";
            li.innerHTML = `<i class="${iconClass}"></i><span>${escapeHTML(titleText)}</span>`;
            li.onclick = () => selectChatHistoryItem(q.id);
            sidebarList.appendChild(li);
        });
    } else if (state.activeQueryId) {
        sidebarList.innerHTML = `<li class="placeholder-item" style="color:var(--text-workspace-muted);font-size:0.8rem;padding:8px 4px;text-align:center;">No past questions</li>`;
    }
}

async function startNewChat() {
    const prevQueryId = state.activeQueryId;
    state.isViewingHistoryItem = false;
    state.activeQueryId = null;
    renderQueryHistory();
    const chatInput = document.getElementById("chat-input");
    if (chatInput) {
        chatInput.value = "";
        chatInput.focus();
    }
    if (prevQueryId) {
        await deleteChatIfInsignificant(prevQueryId);
    }
}

async function selectChatHistoryItem(queryId) {
    const prevQueryId = state.activeQueryId;
    if (prevQueryId === queryId) return;

    state.isViewingHistoryItem = true;
    state.activeQueryId = queryId;
    renderQueryHistory();

    if (prevQueryId) {
        await deleteChatIfInsignificant(prevQueryId);
    }
}

function resetChatFeed() {
    const feed = document.getElementById("chat-feed");
    feed.innerHTML = `
        <div class="welcome-chat-message">
            <i class="fa-solid fa-graduation-cap"></i>
            <h3>Ask Your Tutor Anything</h3>
            <p>Ask a question about your books or notes. Cited passages can be verified side-by-side in real-time.</p>
            <div class="example-chips">
                <button type="button" class="chip-btn" onclick="applyExampleQuestion('Explain reflective equilibrium')">Explain reflective equilibrium</button>
                <button type="button" class="chip-btn" onclick="applyExampleQuestion('Summarize chapter 2')">Summarize chapter 2</button>
                <button type="button" class="chip-btn" onclick="applyExampleQuestion('What is Python?')">What is Python?</button>
            </div>
        </div>
    `;
}

function applyExampleQuestion(text) {
    const input = document.getElementById("chat-input");
    if (input) {
        input.value = text;
        input.focus();
    }
}

async function handleSendChat(event) {
    event.preventDefault();
    const input = document.getElementById("chat-input");
    const query = input.value.trim();
    if (!query) return;

    state.isViewingHistoryItem = false;
    const sourceFilterEl = document.getElementById("chat-source-filter");
    const source_filter = sourceFilterEl ? sourceFilterEl.value : "all";

    // Save activeQueryId before clearing input
    const currentQueryId = state.activeQueryId;

    input.value = "";
    appendMessage("user", query);

    const feed = document.getElementById("chat-feed");
    const loadingBubble = document.createElement("div");
    loadingBubble.className = "message-bubble assistant loading-bubble-temp";
    loadingBubble.innerHTML = `
        <div class="message-avatar"><i class="fa-solid fa-robot"></i></div>
        <div class="message-body">
            <div class="message-text-pane" style="padding:4px 0;">
                <div class="typing-indicator">
                    <span></span>
                    <span></span>
                    <span></span>
                </div>
            </div>
        </div>
    `;
    feed.appendChild(loadingBubble);
    smoothScrollToBottom(feed, 950);

    try {
        const res = await authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/query/text`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ query, source_filter, query_id: currentQueryId })
        });

        const data = await res.json();
        const loading = feed.querySelector(".loading-bubble-temp");
        if (loading) loading.remove();

        if (!res.ok) throw new Error(data.detail || "Query failed");

        // Append response inline — do NOT re-render the whole feed
        appendMessage("assistant", data.answer, data.sources, query, data.id);
        state.activeQueryId = data.id;
        smoothScrollToBottom(feed, 950);

        // Only refresh the sidebar list (not the feed)
        await refreshSidebarOnly();
    } catch (err) {
        const loading = feed.querySelector(".loading-bubble-temp");
        if (loading) loading.remove();
        appendMessage("assistant", `⚠️ Error: ${err.message}`);
    }
}

async function handleCameraPhotoSelected(event) {
    const file = event.target.files[0];
    if (!file) return;

    state.isViewingHistoryItem = false;
    const sourceFilterEl = document.getElementById("chat-source-filter");
    const source_filter = sourceFilterEl ? sourceFilterEl.value : "all";

    const currentQueryId = state.activeQueryId;

    const formData = new FormData();
    formData.append("file", file);
    if (currentQueryId) {
        formData.append("query_id", currentQueryId);
    }

    appendMessage("user", `[Uploaded Note Image: ${file.name}]`);

    const feed = document.getElementById("chat-feed");
    const loadingBubble = document.createElement("div");
    loadingBubble.className = "message-bubble assistant loading-bubble-temp";
    loadingBubble.innerHTML = `
        <div class="message-avatar"><i class="fa-solid fa-robot"></i></div>
        <div class="message-body">
            <div class="message-text-pane" style="padding:4px 0;">
                <div class="typing-indicator">
                    <span></span>
                    <span></span>
                    <span></span>
                </div>
            </div>
        </div>
    `;
    feed.appendChild(loadingBubble);
    smoothScrollToBottom(feed, 950);

    try {
        const res = await authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/query/photo?source_filter=${source_filter}`, {
            method: "POST",
            body: formData
        });
        const data = await res.json();

        const loading = feed.querySelector(".loading-bubble-temp");
        if (loading) loading.remove();

        if (!res.ok) throw new Error(data.detail || "OCR reasoning failed");

        // Append response inline — do NOT re-render the whole feed
        appendMessage("assistant", data.answer, data.sources, `OCR: ${file.name}`, data.id);
        state.activeQueryId = data.id;
        smoothScrollToBottom(feed, 950);

        // Only refresh the sidebar list (not the feed)
        await refreshSidebarOnly();
    } catch (err) {
        const loading = feed.querySelector(".loading-bubble-temp");
        if (loading) loading.remove();
        appendMessage("assistant", `⚠️ OCR Query Error: ${err.message}`);
    } finally {
        event.target.value = "";
    }
}

function triggerVoiceInputIndicator() {
    alert("Voice Dictation:\nPlease dictate your question clearly, or configure your browser's dictation tool to input text directly.");
}

function appendMessage(role, text, citations = [], originalQuery = "", queryId = "", shouldScroll = true) {
    const feed = document.getElementById("chat-feed");
    const welcome = feed.querySelector(".welcome-chat-message");
    if (welcome) welcome.remove();

    const bubble = document.createElement("div");
    bubble.className = `message-bubble ${role}`;
    if (queryId) {
        bubble.setAttribute("data-query-id", queryId);
    }

    const avatarIcon = role === "user" ? "fa-regular fa-user" : "fa-solid fa-robot";
    let bodyHTML = "";

    if (role === "user") {
        bodyHTML = `<div class="message-text-pane">${escapeHTML(text)}</div>`;
    } else {
        bodyHTML = `<div class="message-text-pane">${formatMarkdown(text)}</div>`;

        if (citations && citations.length > 0) {
            bodyHTML += `
                <div class="message-sources-strip">
                    <div class="sources-title">Grounded In</div>
                    <div class="sources-cards-row">
            `;

            const seen = new Set();
            citations.forEach(src => {
                let cardClass = "personal-card";
                let iconClass = "fa-regular fa-file-pdf";
                let label = src.source_name;

                if (src.source_type === "global_book") {
                    cardClass = "textbook-card";
                    iconClass = "fa-regular fa-bookmark";
                } else if (src.source_type === "image_ocr") {
                    cardClass = "note-card";
                    iconClass = "fa-regular fa-image";
                }

                const uniqueKey = `${src.source_type}-${label}-${src.page}-${src.source_id}`;
                if (!seen.has(uniqueKey)) {
                    seen.add(uniqueKey);

                    const pageText = src.page ? `p. ${src.page}` : "Doc";
                    const escapeName = escapeHTML(label);
                    const escapeId = escapeHTML(src.source_id || "");
                    const escapeSection = escapeHTML(src.section || "");

                    bodyHTML += `
                        <div class="source-card-mini ${cardClass}" 
                             onclick="handleCitationMiniCardClick('${src.source_type}', '${escapeId}', '${escapeName}', '${src.page || 1}', '${escapeSection}')"
                             role="button">
                            <i class="${iconClass}"></i>
                            <span class="source-mini-title" title="${escapeName}">${escapeName}</span>
                            <span class="source-mini-meta">${pageText}</span>
                        </div>
                    `;
                }
            });

            bodyHTML += `
                    </div>
                </div>
            `;
        }

        if (text && !text.startsWith("⚠️ Error:") && !text.startsWith("Hello") && !text.startsWith("Hi")) {
            bodyHTML += `
                <div class="chat-action-row">
                    <button class="btn-save-note">
                        <i class="fa-regular fa-bookmark"></i> Save as Study Guide
                    </button>
                </div>
            `;
        }
    }

    bubble.innerHTML = `
        <div class="message-avatar"><i class="${avatarIcon}"></i></div>
        <div class="message-body">${bodyHTML}</div>
    `;

    // Safely attach click listener to save note button without breaking HTML strings
    if (role === "assistant" && text && !text.startsWith("⚠️ Error:") && !text.startsWith("Hello") && !text.startsWith("Hi")) {
        const saveBtn = bubble.querySelector(".btn-save-note");
        if (saveBtn) {
            const cleanQuery = originalQuery.replace(/[^a-zA-Z0-9 ]/g, "").substring(0, 30);
            const titleStr = cleanQuery ? `QA - ${cleanQuery}` : "Saved Study Note";
            saveBtn.addEventListener("click", () => {
                saveMessageAsNote(saveBtn, titleStr, text);
            });
        }
    }

    feed.appendChild(bubble);
    if (shouldScroll) {
        smoothScrollToBottom(feed, 950);
    }

    setTimeout(() => {
        if (window.hljs) {
            bubble.querySelectorAll('pre code').forEach((block) => {
                hljs.highlightElement(block);
            });
        }
        if (window.mermaid) {
            try {
                const unprocessed = feed.querySelectorAll('.mermaid:not([data-processed])');
                if (unprocessed.length > 0) {
                    mermaid.init(undefined, unprocessed);
                }
            } catch (err) {
                console.error("Mermaid parse error:", err);
            }
        }
    }, 100);
}

// ==========================================================================
// FLOATING MODAL OVERLAY SOURCE VIEWER
// ==========================================================================

function closeSourceViewer() {
    const modal = document.getElementById("source-viewer-modal");
    if (modal) modal.classList.add("hidden");

    document.getElementById("pdf-iframe-element").src = "";
    document.getElementById("image-element").src = "";
    document.getElementById("viewer-notes-frame").innerHTML = "";
}

function openSourceViewerPane(breadcrumbText) {
    const modal = document.getElementById("source-viewer-modal");
    if (modal) modal.classList.remove("hidden");

    document.getElementById("viewer-breadcrumb-path").textContent = breadcrumbText;

    document.getElementById("viewer-pdf-frame").classList.add("hidden");
    document.getElementById("viewer-image-frame").classList.add("hidden");
    document.getElementById("viewer-notes-frame").classList.add("hidden");
}

async function openTextbookInNewTab(bookId, page = 1) {
    page = parseInt(page) || 1;
    const url = `${BASE_URL}/subjects/${state.activeSubjectId}/books/${bookId}/view?token=${encodeURIComponent(state.token)}#page=${page}`;
    window.open(url, "_blank", "noopener,noreferrer");
}

async function openPDFSourceViewer(mode, bookId, title, page) {
    openTextbookInNewTab(bookId, page);
}

async function openUploadSourceViewer(sourceType, sourceId, title, page = 1) {
    const url = `${BASE_URL}/subjects/${state.activeSubjectId}/sources/${sourceId}/file?token=${state.token}`;
    window.open(url, "_blank", "noopener,noreferrer");
}

async function openGuideSourceViewer(sourceId, title) {
    openSourceViewerPane(`Library > Study Guides > ${title}`);

    const pane = document.getElementById("viewer-notes-frame");
    pane.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Loading generated notes...`;
    pane.classList.remove("hidden");

    try {
        const res = await authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/sources/${sourceId}/content`);
        if (!res.ok) throw new Error("Could not load generated notes.");
        const data = await res.json();

        let markdownContent = data.content || "";

        // Find if there is a matching source in state to bind citation links to
        let rawDocName = title.replace(/^AI Notes\s*-\s*/i, "").trim().toLowerCase();

        let matchingSource = null;
        if (Array.isArray(state.personalSources)) {
            matchingSource = state.personalSources.find(s => {
                if (s.source_type === "generated_note" || s.source_type === "saved_note") return false;
                let sTitle = s.title.toLowerCase();
                return sTitle.includes(rawDocName) || rawDocName.includes(sTitle.replace(/\.[^/.]+$/, ""));
            });
        }

        let matchingBook = null;
        if (!matchingSource && Array.isArray(state.linkedBookIds)) {
            matchingBook = state.linkedBookIds.find(b => {
                let bTitle = b.title.toLowerCase();
                return bTitle.includes(rawDocName) || rawDocName.includes(bTitle);
            });
        }

        if (matchingSource) {
            const escapeTitle = escapeHTML(matchingSource.title);
            const escapeId = escapeHTML(matchingSource.id);
            markdownContent = markdownContent.replace(/(?:\*?\[Page\s+(\d+)\]\*?)/gi, (match, pageNum) => {
                return `<a href="#" onclick="openUploadSourceViewer('${matchingSource.source_type}', '${escapeId}', '${escapeTitle}', ${pageNum}); return false;" class="inline-citation-link"><i class="fa-regular fa-file-pdf" style="margin-right:3px;"></i>*[Page ${pageNum}]*</a>`;
            });
        } else if (matchingBook) {
            const escapeTitle = escapeHTML(matchingBook.title);
            const escapeId = escapeHTML(matchingBook.id);
            markdownContent = markdownContent.replace(/(?:\*?\[Page\s+(\d+)\]\*?)/gi, (match, pageNum) => {
                return `<a href="#" onclick="openTextbookInNewTab('${escapeId}', ${pageNum}); return false;" class="inline-citation-link"><i class="fa-regular fa-bookmark" style="margin-right:3px;"></i>*[Page ${pageNum}]*</a>`;
            });
        }

        pane.innerHTML = formatMarkdown(markdownContent);

        setTimeout(() => {
            // Run Highlight.js syntax highlighting
            if (window.hljs) {
                pane.querySelectorAll('pre code').forEach((block) => {
                    hljs.highlightElement(block);
                });
            }
            // Run Mermaid rendering (legacy fallback)
            if (window.mermaid) {
                try {
                    const unprocessed = pane.querySelectorAll('.mermaid:not([data-processed])');
                    if (unprocessed.length > 0) {
                        mermaid.init(undefined, unprocessed);
                    }
                } catch (e) {
                    console.error("Mermaid guide parser failed:", e);
                }
            }
        }, 100);
    } catch (err) {
        pane.textContent = "Failed to load notes content: " + err.message;
    }
}

function handleCitationMiniCardClick(sourceType, sourceId, sourceName, page, section) {
    page = parseInt(page) || 1;
    if (sourceType === "global_book") {
        openTextbookInNewTab(sourceId, page);
    } else if (sourceType === "image_ocr") {
        openUploadSourceViewer("image_ocr", sourceId, sourceName, page);
    } else if (sourceType === "saved_note" || sourceType === "generated_note") {
        openGuideSourceViewer(sourceId, sourceName);
    } else {
        // text_pdf
        openUploadSourceViewer("text_pdf", sourceId, sourceName, page);
    }
}

// ==========================================================================
// IMAGE ZOOM & MOVEMENT VIEWPORT CONTROLS
// ==========================================================================

function initImageViewerControls() {
    const viewport = document.getElementById("image-viewport");
    const img = document.getElementById("image-element");

    if (!viewport || !img) return;

    viewport.addEventListener("mousedown", (e) => {
        state.isDraggingImage = true;
        viewport.style.cursor = "grabbing";
        state.imageStartX = e.pageX - viewport.offsetLeft;
        state.imageStartY = e.pageY - viewport.offsetTop;
        state.imageScrollLeft = viewport.scrollLeft;
        state.imageScrollTop = viewport.scrollTop;
    });

    viewport.addEventListener("mouseleave", () => {
        state.isDraggingImage = false;
        viewport.style.cursor = "grab";
    });

    viewport.addEventListener("mouseup", () => {
        state.isDraggingImage = false;
        viewport.style.cursor = "grab";
    });

    viewport.addEventListener("mousemove", (e) => {
        if (!state.isDraggingImage) return;
        e.preventDefault();
        const x = e.pageX - viewport.offsetLeft;
        const y = e.pageY - viewport.offsetTop;
        const walkX = (x - state.imageStartX) * 1.5;
        const walkY = (y - state.imageStartY) * 1.5;
        viewport.scrollLeft = state.imageScrollLeft - walkX;
        viewport.scrollTop = state.imageScrollTop - walkY;
    });
}

function zoomImage(factor) {
    const img = document.getElementById("image-element");
    if (!img) return;
    state.imageZoom = Math.max(0.4, Math.min(4.0, state.imageZoom * factor));
    img.style.transform = `scale(${state.imageZoom})`;
}

function resetImageZoom() {
    const img = document.getElementById("image-element");
    if (!img) return;
    state.imageZoom = 1.0;
    img.style.transform = `scale(1.0)`;
}

// ==========================================================================
// TEXT HELPER FORMATTING FUNCTIONS
// ==========================================================================

function escapeHTML(str) {
    if (!str) return "";
    return str.replace(/[&<>'"]/g,
        tag => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[tag] || tag)
    );
}

function formatMarkdown(text) {
    if (!text) return "";

    // marked.js (v4) handles its own escaping internally — pass raw text directly
    if (window.marked) {
        try {
            let html = marked.parse(text);
            // Replace marked-generated mermaid code blocks with standard divs
            html = html.replace(/<pre><code class="(?:language|lang)-mermaid">([\s\S]*?)<\/code><\/pre>/g, (_, code) => {
                const unescapedCode = code.trim()
                    .replace(/&amp;/g, '&')
                    .replace(/&lt;/g, '<')
                    .replace(/&gt;/g, '>')
                    .replace(/&quot;/g, '"')
                    .replace(/&#39;/g, "'");
                return `<div class="mermaid">${unescapedCode}</div>`;
            });
            return html;
        } catch (e) {
            console.error("Marked.js parse failed, using fallback:", e);
        }
    }

    // ---- Fallback renderer (used only when marked.js is unavailable) ----
    // Pre-escape < and > that are NOT inside fenced code or inline code
    let html = text;
    try {
        const parts = html.split(/(```[\s\S]*?```|`[^`\n]*?`)/g);
        for (let i = 0; i < parts.length; i++) {
            if (i % 2 === 0) {
                parts[i] = parts[i].replace(/</g, "&lt;").replace(/>/g, "&gt;");
            }
        }
        html = parts.join("");
    } catch (e) {
        console.error("Markdown pre-processor error:", e);
    }


    // Fenced code blocks ```lang ... ``` (must come before inline code)
    html = html.replace(/```([\w]*)\n?([\s\S]*?)```/g, (_, lang, code) => {
        const cleanLang = (lang || '').toLowerCase().trim();
        if (cleanLang === 'mermaid') {
            const unescapedCode = code.trim()
                .replace(/&amp;/g, '&')
                .replace(/&lt;/g, '<')
                .replace(/&gt;/g, '>')
                .replace(/&quot;/g, '"')
                .replace(/&#39;/g, "'");
            return `<div class="mermaid">${unescapedCode}</div>`;
        }
        const langLabel = lang ? `<span class="code-lang">${escapeHTML(lang)}</span>` : '';
        return `<pre class="code-block">${langLabel}<code>${escapeHTML(code.trim())}</code></pre>`;
    });

    // Horizontal rules
    html = html.replace(/^---+$/gm, '<hr class="md-hr">');

    // Headers (## before bold so ## doesn't get bolded)
    html = html.replace(/^######\s+(.+)$/gm, '<h6 class="md-h6">$1</h6>');
    html = html.replace(/^#####\s+(.+)$/gm, '<h5 class="md-h5">$1</h5>');
    html = html.replace(/^####\s+(.+)$/gm, '<h4 class="md-h4">$1</h4>');
    html = html.replace(/^###\s+(.+)$/gm, '<h3 class="md-h3">$1</h3>');
    html = html.replace(/^##\s+(.+)$/gm, '<h2 class="md-h2">$1</h2>');
    html = html.replace(/^#\s+(.+)$/gm, '<h1 class="md-h1">$1</h1>');

    // Bold & italic
    html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    html = html.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');

    // Unordered lists (lines starting with * or -)
    html = html.replace(/((?:^[\*\-]\s+.+\n?)+)/gm, (block) => {
        const items = block.trim().split('\n').map(line => {
            const content = line.replace(/^[\*\-]\s+/, '');
            return `<li>${content}</li>`;
        }).join('');
        return `<ul class="md-ul">${items}</ul>`;
    });

    // Ordered lists
    html = html.replace(/((?:^\d+\.\s+.+\n?)+)/gm, (block) => {
        const items = block.trim().split('\n').map(line => {
            const content = line.replace(/^\d+\.\s+/, '');
            return `<li>${content}</li>`;
        }).join('');
        return `<ol class="md-ol">${items}</ol>`;
    });

    // GFM Tables — must run BEFORE paragraph conversion
    // Matches a table block: header row | separator row | data rows (all lines starting with |)
    html = html.replace(/(\|.+\|\n\|[-| :]+\|\n(?:\|.+\|\n?)*)/g, (block) => {
        const lines = block.trim().split('\n');
        if (lines.length < 2) return block;

        const parseRow = (line) => line.replace(/^\||\|$/g, '').split('|').map(c => c.trim());

        const headers = parseRow(lines[0]);
        // lines[1] is the separator, skip it
        const bodyRows = lines.slice(2);

        const thead = '<tr>' + headers.map(h => `<th>${h}</th>`).join('') + '</tr>';
        const tbody = bodyRows.map(row => {
            const cells = parseRow(row);
            return '<tr>' + cells.map(c => `<td>${c}</td>`).join('') + '</tr>';
        }).join('');

        return `<div class="md-table-wrap"><table class="md-table"><thead>${thead}</thead><tbody>${tbody}</tbody></table></div>\n`;
    });

    // Paragraphs — double newlines become paragraph breaks
    html = html.replace(/\n{2,}/g, '</p><p class="md-p">');
    html = `<p class="md-p">${html}</p>`;

    // Clean up empty paragraphs around block elements
    html = html.replace(/<p class="md-p">(<(?:h[1-6]|ul|ol|pre|hr|div)[^>]*>)/g, '$1');
    html = html.replace(/(<\/(?:h[1-6]|ul|ol|pre|hr|div)>)<\/p>/g, '$1');
    html = html.replace(/<p class="md-p"><\/p>/g, '');

    return html;
}

// ==========================================================================
// ACTION & MODAL OVERLAY TOGGLERS
// ==========================================================================

async function saveMessageAsNote(button, title, content) {
    button.disabled = true;
    const originalText = button.innerHTML;
    button.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Saving...`;

    try {
        const res = await authFetch(`${BASE_URL}/subjects/${state.activeSubjectId}/saved-notes`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title, content })
        });

        if (!res.ok) throw new Error("Failed to save note");

        button.innerHTML = `<i class="fa-solid fa-circle-check"></i> Saved to Notes`;
        await refreshSubjectData();
        showToast("Saved study guide successfully!", "success");
    } catch (err) {
        alert(err.message);
        button.disabled = false;
        button.innerHTML = originalText;
    }
}

// Modal open/close bindings
function openCatalogModal() {
    document.getElementById("catalog-modal").classList.remove("hidden");
}
function closeCatalogModal() {
    document.getElementById("catalog-modal").classList.add("hidden");
    document.getElementById("openstax-search-input").value = "";
    document.getElementById("global-books-list").innerHTML = `<li class="placeholder-item">Enter a topic to search OpenStax, LibreTexts or Project Gutenberg.</li>`;
}

function openSubjectModal() {
    document.getElementById("subject-modal").classList.remove("hidden");
}
function closeSubjectModal() {
    document.getElementById("subject-modal").classList.add("hidden");
    document.getElementById("new-subject-name").value = "";
}

function showToast(message, type = "info") {
    const container = document.getElementById("toast-container");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = `toast ${type}`;

    let icon = "fa-solid fa-info-circle";
    if (type === "success") icon = "fa-solid fa-circle-check";
    if (type === "error") icon = "fa-solid fa-triangle-exclamation";

    toast.innerHTML = `<i class="${icon}"></i> <span>${escapeHTML(message)}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.animation = "toastSlideOut 0.3s cubic-bezier(0.16, 1, 0.3, 1) forwards";
        setTimeout(() => {
            toast.remove();
        }, 300);
    }, 3000);
}

function smoothScrollToBottom(element) {
    if (!element) return;
    element.scrollTo({
        top: element.scrollHeight,
        behavior: "smooth"
    });
}

function customConfirm(message) {
    return new Promise((resolve) => {
        const modal = document.getElementById("custom-confirm-modal");
        const messageEl = document.getElementById("custom-confirm-message");
        const yesBtn = document.getElementById("custom-confirm-yes");
        const noBtn = document.getElementById("custom-confirm-no");

        messageEl.textContent = message;
        modal.classList.remove("hidden");

        const cleanup = (value) => {
            modal.classList.add("hidden");
            yesBtn.onclick = null;
            noBtn.onclick = null;
            resolve(value);
        };

        yesBtn.onclick = () => cleanup(true);
        noBtn.onclick = () => cleanup(false);
    });
}

