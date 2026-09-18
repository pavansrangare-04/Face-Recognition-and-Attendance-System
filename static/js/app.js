document.addEventListener("DOMContentLoaded", () => {
    function escapeHtml(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/\"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    // State Variables
    let currentTab = "dashboard";
    let isCameraActive = true;
    let registeredStudents = [];
    let attendanceLogs = [];
    let knownLogIds = new Set();
    let sessionLogsCount = 0;
    let initialLoad = true;

    // Elements
    const menuItems = document.querySelectorAll(".menu-item");
    const tabContents = document.querySelectorAll(".tab-content");
    const pageTitle = document.getElementById("page-title");
    const pageSubtitle = document.getElementById("page-subtitle");
    const dateTimeDisplay = document.getElementById("current-date-time");
    const btnLogout = document.getElementById("btn-logout");

    // Camera elements
    const btnToggleCamera = document.getElementById("btn-toggle-camera");
    const btnToggleCameraText = document.getElementById("btn-toggle-camera-text");
    const cameraStream = document.getElementById("camera-stream");
    const cameraPlaceholder = document.getElementById("camera-placeholder");
    const liveIndicator = document.getElementById("live-indicator");
    const btnStartCameraPlaceholder = document.getElementById("btn-start-camera-placeholder");
    const systemStatusIndicator = document.getElementById("system-status-indicator");
    const systemStatusText = document.getElementById("system-status-text");

    // Modal elements
    const registerModal = document.getElementById("register-modal");
    const btnOpenRegisterModal = document.getElementById("btn-open-register-modal");
    const btnOpenCameraRegister = document.getElementById("btn-open-camera-register");
    const btnCloseModal = document.getElementById("btn-close-modal");
    const btnCancelModal = document.getElementById("btn-cancel-modal");
    const registerForm = document.getElementById("register-student-form");
    const uploadArea = document.getElementById("upload-area");
    const fileInput = document.getElementById("student-photo");
    const uploadPlaceholder = document.getElementById("upload-placeholder");
    const uploadPreview = document.getElementById("upload-preview");
    const previewImg = document.getElementById("preview-img");
    const btnRemovePreview = document.getElementById("btn-remove-preview");
    const btnOpenCameraLive = document.getElementById("btn-open-camera-live");
    const btnTakeCameraPhoto = document.getElementById("btn-take-camera-photo");
    const btnCloseCameraLive = document.getElementById("btn-close-camera-live");
    const btnClearCapturePhoto = document.getElementById("btn-clear-capture-photo");
    const cameraCaptureArea = document.getElementById("camera-capture-area");
    const cameraLiveWrapper = document.getElementById("camera-live-wrapper");
    const cameraVideo = document.getElementById("camera-video");
    const cameraOverlay = document.getElementById("camera-overlay");
    const capturedPhotoWrapper = document.getElementById("captured-photo-wrapper");
    const capturedPhotoImg = document.getElementById("captured-photo-img");
    const cameraInstructions = document.getElementById("camera-instructions");
    const cameraControlActions = document.getElementById("camera-control-actions");
    const btnRetakePhoto = document.getElementById("btn-retake-photo");
    const btnUsePhoto = document.getElementById("btn-use-photo");
    const btnSubmitRegistration = document.getElementById("btn-submit-registration");
    const registrationSpinner = document.getElementById("registration-spinner");
    let capturedBlob = null;
    let activePreviewUrl = null;
    let localCameraStream = null;
    let pendingCaptureDataUrl = null;
    let localFaceDetected = false;
    let faceDetector = null;
    let cameraDetectionFrame = null;
    let detectionInProgress = false;
    const cameraAnalysisCanvas = document.createElement("canvas");
    cameraAnalysisCanvas.width = 160;
    cameraAnalysisCanvas.height = 120;

    // Dashboard metrics
    const statTotalStudents = document.getElementById("stat-total-students");
    const statPresentToday = document.getElementById("stat-present-today");
    const statAbsentToday = document.getElementById("stat-absent-today");
    const statLastActive = document.getElementById("stat-last-active");

    // Lists & Tables
    const liveLogsContainer = document.getElementById("live-logs-container");
    const liveLogsEmpty = document.getElementById("live-logs-empty");
    const sessionCountBadge = document.getElementById("session-count-badge");
    const studentGrid = document.getElementById("student-grid");
    const attendanceTableBody = document.getElementById("attendance-table-body");
    const logsEmptyState = document.getElementById("logs-empty-state");

    // Filters
    const studentSearchInput = document.getElementById("student-search-input");
    const logSearchInput = document.getElementById("log-search-input");
    const logDateFilter = document.getElementById("log-date-filter");
    const btnClearDateFilter = document.getElementById("btn-clear-date-filter");
    const btnClearLogs = document.getElementById("btn-clear-logs");

    // Analytics elements
    const progressRing = document.getElementById("analytics-progress-ring");
    const analyticsPercentage = document.getElementById("analytics-percentage");
    const analyticsPresentCount = document.getElementById("analytics-present-count");
    const analyticsAbsentCount = document.getElementById("analytics-absent-count");
    const analyticsTotalCount = document.getElementById("analytics-total-count");

    // Toast container
    const toastContainer = document.getElementById("toast-container");

    // --- Web Audio API Synth Sound ---
    function playSuccessChime() {
        try {
            const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            
            // Note 1 (D5)
            const osc1 = audioCtx.createOscillator();
            const gain1 = audioCtx.createGain();
            osc1.type = "sine";
            osc1.frequency.setValueAtTime(587.33, audioCtx.currentTime); // D5
            gain1.gain.setValueAtTime(0, audioCtx.currentTime);
            gain1.gain.linearRampToValueAtTime(0.2, audioCtx.currentTime + 0.05);
            gain1.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.25);
            osc1.connect(gain1);
            gain1.connect(audioCtx.destination);
            osc1.start();
            osc1.stop(audioCtx.currentTime + 0.3);

            // Note 2 (A5) after a small delay
            setTimeout(() => {
                const osc2 = audioCtx.createOscillator();
                const gain2 = audioCtx.createGain();
                osc2.type = "sine";
                osc2.frequency.setValueAtTime(880.00, audioCtx.currentTime); // A5
                gain2.gain.setValueAtTime(0, audioCtx.currentTime);
                gain2.gain.linearRampToValueAtTime(0.25, audioCtx.currentTime + 0.05);
                gain2.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.35);
                osc2.connect(gain2);
                gain2.connect(audioCtx.destination);
                osc2.start();
                osc2.stop(audioCtx.currentTime + 0.4);
            }, 100);

        } catch (e) {
            console.warn("AudioContext chime failed:", e);
        }
    }

    // --- Real-Time Date Clock ---
    function updateClock() {
        const now = new Date();
        const options = { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' };
        const dateStr = now.toLocaleDateString('en-US', options);
        const timeStr = now.toLocaleTimeString('en-US', { hour12: true, hour: '2-digit', minute: '2-digit', second: '2-digit' });
        dateTimeDisplay.textContent = `${dateStr} | ${timeStr}`;
    }
    setInterval(updateClock, 1000);
    updateClock();

    // --- Toast Notification System ---
    function showToast(title, message, type = "success") {
        const toast = document.createElement("div");
        toast.className = `toast ${type}`;
        
        const icon = type === "success" ? "✓" : type === "danger" ? "✕" : "ℹ";
        const safeTitle = escapeHtml(title);
        const safeMessage = escapeHtml(message);
        
        toast.innerHTML = `
            <div class="toast-icon">${icon}</div>
            <div class="toast-content">
                <h4>${safeTitle}</h4>
                <p>${safeMessage}</p>
            </div>
        `;
        
        toastContainer.appendChild(toast);
        
        // Remove toast after animation completes (4s display)
        setTimeout(() => {
            toast.style.animation = "fadeIn 0.35s reverse forwards";
            setTimeout(() => {
                toast.remove();
            }, 350);
        }, 4000);
    }

    // --- Theme Toggle ---
    const themeToggleBtn = document.getElementById("btn-theme-toggle");
    const themeLabel = document.getElementById("theme-label");

    function applyTheme(theme) {
        if (theme === "light") {
            document.documentElement.classList.remove("dark-theme");
            document.documentElement.classList.add("light-theme");
            themeLabel.textContent = "Light";
            localStorage.setItem("appTheme", "light");
        } else {
            document.documentElement.classList.remove("light-theme");
            document.documentElement.classList.add("dark-theme");
            themeLabel.textContent = "Dark";
            localStorage.setItem("appTheme", "dark");
        }
    }

    themeToggleBtn?.addEventListener("click", () => {
        const currentTheme = document.documentElement.classList.contains("light-theme") ? "light" : "dark";
        applyTheme(currentTheme === "dark" ? "light" : "dark");
        showToast("Theme Updated", `Switched to ${themeLabel.textContent} mode.`, "success");
    });

    const savedTheme = localStorage.getItem("appTheme") || "dark";
    applyTheme(savedTheme);

    // --- Dynamic Navigation ---
    menuItems.forEach(item => {
        item.addEventListener("click", (e) => {
            e.preventDefault();
            const tabName = item.getAttribute("data-tab");
            
            // Toggle active sidebar link
            menuItems.forEach(mi => mi.classList.remove("active"));
            item.classList.add("active");
            
            // Toggle viewport tabs
            tabContents.forEach(content => content.classList.remove("active"));
            document.getElementById(`tab-${tabName}`).classList.add("active");
            
            currentTab = tabName;
            
            // Change page title & labels
            if (tabName === "dashboard") {
                pageTitle.textContent = "Dashboard";
                pageSubtitle.textContent = "Real-time attendance tracking and face scanning.";
            } else if (tabName === "students") {
                pageTitle.textContent = "Student Directory";
                pageSubtitle.textContent = "Manage registered students and facial data.";
                fetchStudents();
            } else if (tabName === "logs") {
                pageTitle.textContent = "Attendance Records";
                pageSubtitle.textContent = "View, filter, and export CSV logs.";
                fetchAttendanceLogs();
            } else if (tabName === "analytics") {
                pageTitle.textContent = "System Analytics";
                pageSubtitle.textContent = "Visualize presence rates and metrics.";
                updateAnalyticsRing();
            }
        });
    });

    // --- Camera Control functions ---
    function setCameraUIState(active) {
        isCameraActive = active;
        if (active) {
            cameraStream.classList.add("active");
            cameraPlaceholder.classList.add("hidden");
            liveIndicator.style.display = "flex";
            btnToggleCamera.className = "btn btn-secondary btn-icon";
            btnToggleCameraText.textContent = "Stop Feed";
            
            systemStatusIndicator.className = "status-pulse";
            systemStatusText.textContent = "Camera Stream Active";
            
            // Reset src to force browser reconnect to feed
            cameraStream.src = "/video_feed?" + new Date().getTime();
        } else {
            cameraStream.classList.remove("active");
            cameraPlaceholder.classList.remove("hidden");
            liveIndicator.style.display = "none";
            btnToggleCamera.className = "btn btn-primary btn-icon";
            btnToggleCameraText.textContent = "Start Feed";
            
            systemStatusIndicator.className = "status-pulse inactive";
            systemStatusText.textContent = "Camera Stream Halted";
            
            cameraStream.src = "";
        }
    }

    async function checkCameraStatus() {
        try {
            const res = await fetch("/api/camera/status");
            const data = await res.json();
            setCameraUIState(data.camera_active);
        } catch (err) {
            console.error("Error checking camera status:", err);
        }
    }

    async function toggleCamera(action) {
        try {
            const res = await fetch("/api/camera/toggle", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ action: action })
            });
            const data = await res.json();
            setCameraUIState(data.camera_active);
            showToast(
                data.camera_active ? "Webcam Active" : "Webcam Off", 
                data.camera_active ? "Webcam has been turned ON." : "Webcam has been turned OFF.",
                data.camera_active ? "success" : "neutral"
            );
        } catch (err) {
            showToast("Camera Error", "Unable to communicate with the camera backend.", "danger");
        }
    }

    btnToggleCamera.addEventListener("click", () => toggleCamera("toggle"));
    btnStartCameraPlaceholder.addEventListener("click", () => toggleCamera("start"));

    // --- Student Directory Functions ---
    async function fetchStudents() {
        try {
            const res = await fetch("/api/students");
            const data = await res.json();
            registeredStudents = data.students;

            // Render warning banner if any training issue
            const warningBanner = document.getElementById("model-training-warning");
            const warningText = document.getElementById("model-training-warning-text");
            if (data.training_error) {
                warningBanner.classList.remove("hidden");
                warningText.textContent = data.training_error;
                
                systemStatusIndicator.className = "status-pulse warning";
                systemStatusText.textContent = "Model Alert: " + data.training_error;
            } else {
                warningBanner.classList.add("hidden");
            }

            renderStudentsGrid();
            
            // Update Dashboard Counters
            statTotalStudents.textContent = data.students.length;
            
        } catch (err) {
            console.error("Failed to load students:", err);
            showToast("Database Error", "Failed to retrieve student directory.", "danger");
        }
    }

    function renderStudentsGrid() {
        const query = studentSearchInput.value.toLowerCase().trim();
        const filtered = registeredStudents.filter(s => s.name.toLowerCase().includes(query));
        
        // Remove old cards (keep empty state template reference)
        const emptyState = studentGrid.querySelector(".empty-state");
        studentGrid.innerHTML = "";
        
        if (filtered.length === 0) {
            studentGrid.appendChild(emptyState);
            emptyState.classList.remove("hidden");
            // Setup correct event click
            const btnAddEmpty = document.getElementById("btn-add-student-empty");
            if (btnAddEmpty) {
                btnAddEmpty.addEventListener("click", openRegisterModal);
            }
            return;
        }

        filtered.forEach(student => {
            const card = document.createElement("div");
            card.className = "student-card";
            const safeName = escapeHtml(student.name);
            const safePhoto = escapeHtml(student.photo_url || '');
            const safeFilename = escapeHtml(student.filename || '');
            card.innerHTML = `
                <div class="student-photo-wrapper">
                    <img src="${safePhoto}" alt="${safeName}" onerror="this.src='https://placehold.co/200x200?text=No+Photo'">
                </div>
                <div class="student-info">
                    <h3>${safeName}</h3>
                    <span>Registered: ${escapeHtml((student.registered_at || '').split(' ')[0])}</span>
                    <div class="student-actions">
                        <button class="btn btn-danger btn-sm delete-btn" data-filename="${safeFilename}">
                            Delete Profile
                        </button>
                    </div>
                </div>
            `;
            studentGrid.appendChild(card);
        });

        // Set up delete event listeners
        studentGrid.querySelectorAll(".delete-btn").forEach(btn => {
            btn.addEventListener("click", (e) => {
                const filename = e.target.getAttribute("data-filename");
                if (confirm(`Are you sure you want to delete the student profile "${filename.replace('.jpg', '').replace('_', ' ')}"?`)) {
                    deleteStudent(filename);
                }
            });
        });
    }

    async function deleteStudent(filename) {
        try {
            const res = await fetch(`/api/students/${filename}`, { method: "DELETE" });
            const data = await res.json();
            if (data.success) {
                showToast("Profile Deleted", data.message, "success");
                fetchStudents();
                updateDashboardStats();
            } else {
                showToast("Deletion Failed", data.message, "danger");
            }
        } catch (err) {
            showToast("Server Error", "Could not complete deletion request.", "danger");
        }
    }

    studentSearchInput.addEventListener("input", renderStudentsGrid);

    // --- Modal Registration Operations ---
    function openRegisterModal(openCamera = false) {
        registerModal.classList.add("active");
        registerForm.reset();
        clearUploadPreview();
        if (openCamera) {
            openCameraPreview();
        }
    }

    function closeRegisterModal() {
        registerModal.classList.remove("active");
        closeCameraPreview();
    }

    function updatePreviewFromBlob(blob) {
        capturedBlob = blob;
        if (activePreviewUrl) {
            URL.revokeObjectURL(activePreviewUrl);
        }
        activePreviewUrl = URL.createObjectURL(blob);
        previewImg.src = activePreviewUrl;
        uploadPlaceholder.classList.add("hidden");
        uploadPreview.classList.remove("hidden");
    }

    function setCameraInstruction(message, warning = false) {
        cameraInstructions.textContent = message;
        cameraInstructions.classList.toggle("warning", warning);
    }

    function stopLocalCamera() {
        if (cameraDetectionFrame) {
            cancelAnimationFrame(cameraDetectionFrame);
            cameraDetectionFrame = null;
        }
        if (localCameraStream) {
            localCameraStream.getTracks().forEach(track => track.stop());
            localCameraStream = null;
        }
        if (cameraVideo) {
            cameraVideo.pause();
            cameraVideo.srcObject = null;
        }
        localFaceDetected = false;
        detectionInProgress = false;
    }

    async function startLocalCamera() {
        if (!navigator.mediaDevices?.getUserMedia) {
            showToast("Camera Error", "Your browser does not support webcam capture.", "danger");
            setCameraInstruction("Browser camera not supported.", true);
            return false;
        }

        try {
            localCameraStream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } },
                audio: false
            });
            cameraVideo.srcObject = localCameraStream;
            cameraVideo.addEventListener("loadedmetadata", updateCameraCanvasSize, { once: true });
            await cameraVideo.play();
            updateCameraCanvasSize();
            setCameraInstruction("Face detection is starting. Keep your face centered and well lit.");
            return true;
        } catch (err) {
            console.error("Local camera error", err);
            showToast("Camera Access Denied", "Grant webcam access to capture a photo.", "danger");
            setCameraInstruction("Unable to access the webcam.", true);
            return false;
        }
    }

    function updateCameraCanvasSize() {
        if (!cameraVideo || !cameraOverlay) return;
        cameraOverlay.width = cameraVideo.videoWidth || 640;
        cameraOverlay.height = cameraVideo.videoHeight || 480;
        cameraOverlay.style.width = "100%";
        cameraOverlay.style.height = "100%";
    }

    function getAverageBrightness() {
        try {
            const ctx = cameraAnalysisCanvas.getContext("2d");
            ctx.drawImage(cameraVideo, 0, 0, cameraAnalysisCanvas.width, cameraAnalysisCanvas.height);
            const imageData = ctx.getImageData(0, 0, cameraAnalysisCanvas.width, cameraAnalysisCanvas.height).data;
            let total = 0;
            let count = 0;
            for (let i = 0; i < imageData.length; i += 4) {
                total += (imageData[i] * 0.299 + imageData[i + 1] * 0.587 + imageData[i + 2] * 0.114);
                count += 1;
            }
            return total / count;
        } catch (err) {
            return null;
        }
    }

    function renderFaceGuide(face, brightness) {
        const ctx = cameraOverlay.getContext("2d");
        const width = cameraOverlay.width;
        const height = cameraOverlay.height;
        ctx.clearRect(0, 0, width, height);

        if (face) {
            ctx.strokeStyle = "#5eead4";
            ctx.lineWidth = 3;
            ctx.strokeRect(face.x, face.y, face.width, face.height);
            ctx.fillStyle = "rgba(0, 0, 0, 0.35)";
            ctx.fillRect(0, 0, width, 34);
            ctx.fillStyle = "#fff";
            ctx.font = "16px Inter, sans-serif";
            ctx.fillText("Face detected. Align it inside the box.", 14, 22);

            const centerX = face.x + face.width / 2;
            const centerY = face.y + face.height / 2;
            const horizontalOffset = Math.abs(centerX - width / 2);
            const verticalOffset = Math.abs(centerY - height / 2);
            const faceRatio = face.width / width;

            if (brightness !== null && brightness < 70) {
                setCameraInstruction("Move to a brighter area.", true);
            } else if (brightness !== null && brightness > 220) {
                setCameraInstruction("Avoid strong backlight.", true);
            } else if (faceRatio < 0.18) {
                setCameraInstruction("Move closer so the face fills more of the frame.", true);
            } else if (faceRatio > 0.55) {
                setCameraInstruction("Move farther away so the face fits better.", true);
            } else if (horizontalOffset > width * 0.14 || verticalOffset > height * 0.14) {
                setCameraInstruction("Keep your face centered in the frame.", true);
            } else {
                setCameraInstruction("Face detected. Keep your face straight and clearly visible.");
            }
            localFaceDetected = true;
        } else {
            ctx.strokeStyle = "rgba(255,255,255,0.55)";
            ctx.lineWidth = 2;
            const padding = 26;
            ctx.setLineDash([8, 6]);
            ctx.strokeRect(padding, padding, width - padding * 2, height - padding * 2);
            ctx.setLineDash([]);
            ctx.fillStyle = "rgba(0, 0, 0, 0.35)";
            ctx.fillRect(0, 0, width, 34);
            ctx.fillStyle = "#fff";
            ctx.font = "16px Inter, sans-serif";
            ctx.fillText("Face not detected yet. Position your face inside the frame.", 14, 22);

            if (brightness !== null && brightness < 70) {
                setCameraInstruction("Move to a brighter area. Keep your face inside the frame.", true);
            } else if (brightness !== null && brightness > 220) {
                setCameraInstruction("Avoid strong backlight and keep the face visible.", true);
            } else {
                setCameraInstruction("Position your face inside the frame.");
            }
            localFaceDetected = false;
        }
    }

    async function runCameraDetectionLoop() {
        if (!cameraLiveWrapper.classList.contains("hidden") && localCameraStream) {
            if (!detectionInProgress && window.FaceDetector) {
                detectionInProgress = true;
                try {
                    if (!faceDetector) {
                        faceDetector = new window.FaceDetector({ fastMode: true, maxDetectedFaces: 1 });
                    }
                    const faces = await faceDetector.detect(cameraVideo);
                    const brightness = getAverageBrightness();
                    const face = faces && faces.length ? faces[0].boundingBox : null;
                    renderFaceGuide(face, brightness);
                } catch (err) {
                    const brightness = getAverageBrightness();
                    renderFaceGuide(null, brightness);
                } finally {
                    detectionInProgress = false;
                }
            } else if (!window.FaceDetector) {
                const brightness = getAverageBrightness();
                renderFaceGuide(null, brightness);
            }
            cameraDetectionFrame = requestAnimationFrame(runCameraDetectionLoop);
        }
    }

    async function openCameraPreview() {
        cameraCaptureArea.classList.remove("hidden");
        cameraLiveWrapper.classList.remove("hidden");
        capturedPhotoWrapper.classList.add("hidden");
        cameraControlActions.classList.add("hidden");
        setCameraInstruction("Opening camera preview... make sure the student is ready.");

        const started = await startLocalCamera();
        if (started) {
            updateCameraCanvasSize();
            runCameraDetectionLoop();
        }
    }

    function closeCameraPreview() {
        cameraCaptureArea.classList.add("hidden");
        cameraLiveWrapper.classList.add("hidden");
        capturedPhotoWrapper.classList.add("hidden");
        cameraControlActions.classList.add("hidden");
        setCameraInstruction("Open the camera and position the face inside the frame before capturing.");
        stopLocalCamera();
    }

    async function captureFromCamera() {
        if (!localCameraStream || cameraVideo.paused || cameraVideo.ended) {
            showToast("Camera Error", "Please open the live camera preview before capturing.", "danger");
            return;
        }

        const captureCanvas = document.createElement("canvas");
        captureCanvas.width = cameraVideo.videoWidth;
        captureCanvas.height = cameraVideo.videoHeight;
        const ctx = captureCanvas.getContext("2d");
        ctx.drawImage(cameraVideo, 0, 0, captureCanvas.width, captureCanvas.height);

        const dataUrl = captureCanvas.toDataURL("image/jpeg", 0.9);
        pendingCaptureDataUrl = dataUrl;

        capturedPhotoImg.src = dataUrl;
        cameraLiveWrapper.classList.add("hidden");
        capturedPhotoWrapper.classList.remove("hidden");
        cameraControlActions.classList.remove("hidden");
        setCameraInstruction(localFaceDetected ? "Photo captured. Review it and use this photo or retake." : "Photo captured. If the face is not visible, retake it.", !localFaceDetected);
    }

    async function useCapturedPhoto() {
        if (!pendingCaptureDataUrl) {
            showToast("No Photo", "Capture an image before using it.", "danger");
            return;
        }

        const response = await fetch(pendingCaptureDataUrl);
        const blob = await response.blob();
        capturedBlob = blob;

        if (activePreviewUrl) {
            URL.revokeObjectURL(activePreviewUrl);
        }
        activePreviewUrl = URL.createObjectURL(blob);
        previewImg.src = activePreviewUrl;
        uploadPlaceholder.classList.add("hidden");
        uploadPreview.classList.remove("hidden");

        setCameraInstruction("Captured photo saved. Enter the student name and submit.");
        closeCameraPreview();
    }

    async function retakeCameraPhoto() {
        if (!localCameraStream) {
            await startLocalCamera();
        }
        cameraLiveWrapper.classList.remove("hidden");
        capturedPhotoWrapper.classList.add("hidden");
        cameraControlActions.classList.add("hidden");
        setCameraInstruction("Position the face again and capture when ready.");
        runCameraDetectionLoop();
    }

    function clearUploadPreview() {
        uploadPreview.classList.add("hidden");
        uploadPlaceholder.classList.remove("hidden");
        previewImg.src = "";
        fileInput.value = "";
        capturedBlob = null;
        if (activePreviewUrl) {
            URL.revokeObjectURL(activePreviewUrl);
            activePreviewUrl = null;
        }
    }

    btnOpenRegisterModal.addEventListener("click", openRegisterModal);
    btnCloseModal.addEventListener("click", closeRegisterModal);
    btnCancelModal.addEventListener("click", closeRegisterModal);

    // File Drag & Drop preview logic
    uploadArea.addEventListener("dragover", (e) => {
        e.preventDefault();
        uploadArea.classList.add("dragover");
    });
    uploadArea.addEventListener("dragleave", () => {
        uploadArea.classList.remove("dragover");
    });
    uploadArea.addEventListener("drop", (e) => {
        e.preventDefault();
        uploadArea.classList.remove("dragover");
        if (e.dataTransfer.files.length) {
            fileInput.files = e.dataTransfer.files;
            handleFileSelect(e.dataTransfer.files[0]);
        }
    });

    fileInput.addEventListener("change", (e) => {
        if (e.target.files.length) {
            handleFileSelect(e.target.files[0]);
        }
    });

    btnOpenCameraRegister?.addEventListener("click", () => openRegisterModal(true));
    btnOpenCameraLive?.addEventListener("click", openCameraPreview);
    btnTakeCameraPhoto?.addEventListener("click", captureFromCamera);
    btnCloseCameraLive?.addEventListener("click", closeCameraPreview);
    btnClearCapturePhoto?.addEventListener("click", () => {
        clearUploadPreview();
        closeCameraPreview();
    });
    btnRetakePhoto?.addEventListener("click", retakeCameraPhoto);
    btnUsePhoto?.addEventListener("click", useCapturedPhoto);

    function handleFileSelect(file) {
        if (!file.type.startsWith("image/")) {
            showToast("File Error", "Please select a valid image file (JPG, PNG).", "danger");
            return;
        }
        capturedBlob = null;
        const reader = new FileReader();
        reader.onload = (e) => {
            if (activePreviewUrl) {
                URL.revokeObjectURL(activePreviewUrl);
                activePreviewUrl = null;
            }
            previewImg.src = e.target.result;
            uploadPlaceholder.classList.add("hidden");
            uploadPreview.classList.remove("hidden");
        };
        reader.readAsDataURL(file);
    }

    btnRemovePreview.addEventListener("click", (e) => {
        e.stopPropagation();
        clearUploadPreview();
    });

    registerForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const nameVal = document.getElementById("student-name").value.trim();
        const file = fileInput.files[0];
        const imageSource = capturedBlob || file;

        if (!nameVal || !imageSource) {
            showToast("Validation Error", "All fields are required.", "danger");
            return;
        }

        // Show spinner / loading state
        btnSubmitRegistration.disabled = true;
        registrationSpinner.classList.remove("hidden");

        const formData = new FormData();
        formData.append("name", nameVal);
        if (capturedBlob) {
            formData.append("image", capturedBlob, "camera-capture.jpg");
        } else {
            formData.append("image", file);
        }

        try {
            const res = await fetch("/api/students", {
                method: "POST",
                body: formData
            });
            const data = await res.json();
            
            if (data.success) {
                showToast("Student Registered", data.message, "success");
                closeRegisterModal();
                fetchStudents();
                updateDashboardStats();
            } else {
                showToast("Registration Failed", data.message, "danger");
            }
        } catch (err) {
            showToast("Server Error", "An error occurred during submission.", "danger");
        } finally {
            btnSubmitRegistration.disabled = false;
            registrationSpinner.classList.add("hidden");
        }
    });

    // --- Historical Logs Filtering & Display ---
    async function fetchAttendanceLogs() {
        try {
            const res = await fetch("/api/attendance");
            const data = await res.json();
            attendanceLogs = data.records;
            renderLogsTable();
        } catch (err) {
            console.error("Failed to fetch CSV logs:", err);
            showToast("Error loading logs", "Unable to load CSV attendance data.", "danger");
        }
    }

    function renderLogsTable() {
        const query = logSearchInput.value.toLowerCase().trim();
        const dateVal = logDateFilter.value; // YYYY-MM-DD
        
        let filtered = attendanceLogs;
        
        if (query) {
            filtered = filtered.filter(l => l.name.toLowerCase().includes(query));
        }
        if (dateVal) {
            filtered = filtered.filter(l => l.date === dateVal);
        }

        attendanceTableBody.innerHTML = "";
        
        if (filtered.length === 0) {
            logsEmptyState.classList.remove("hidden");
            return;
        }
        
        logsEmptyState.classList.add("hidden");
        
        filtered.forEach((log, index) => {
            const row = document.createElement("tr");
            const safeName = escapeHtml(log.name || 'Unknown');
            row.innerHTML = `
                <td>#${escapeHtml(log.id)}</td>
                <td style="font-weight:600; color: white;">${safeName}</td>
                <td>${escapeHtml(log.date || '')}</td>
                <td>${escapeHtml(log.time || '')}</td>
                <td><span class="badge">Present</span></td>
            `;
            attendanceTableBody.appendChild(row);
        });
    }

    logSearchInput.addEventListener("input", renderLogsTable);
    logDateFilter.addEventListener("change", renderLogsTable);
    
    btnClearDateFilter.addEventListener("click", () => {
        logSearchInput.value = "";
        logDateFilter.value = "";
        renderLogsTable();
    });

    btnClearLogs.addEventListener("click", async () => {
        if (confirm("Are you sure you want to permanently delete all historical attendance records? This cannot be undone.")) {
            try {
                const res = await fetch("/api/attendance", { method: "DELETE" });
                const data = await res.json();
                if (data.success) {
                    showToast("Logs Cleared", data.message, "success");
                    fetchAttendanceLogs();
                    updateDashboardStats();
                    // Clear session logs too
                    liveLogsContainer.innerHTML = "";
                    liveLogsContainer.appendChild(liveLogsEmpty);
                    liveLogsEmpty.style.display = "flex";
                    sessionCountBadge.textContent = "0 Logs";
                    sessionLogsCount = 0;
                } else {
                    showToast("Action Failed", data.message, "danger");
                }
            } catch (err) {
                showToast("Error resetting logs", "Server error resetting CSV log database.", "danger");
            }
        }
    });

    // --- Dashboard Metrics & Background Polling ---
    async function updateDashboardStats() {
        try {
            const res = await fetch("/api/stats");
            const stats = await res.json();
            
            // Dashboard counters
            statTotalStudents.textContent = stats.total_students;
            statPresentToday.textContent = stats.total_present_today;
            statAbsentToday.textContent = stats.total_absent_today;
            statLastActive.textContent = stats.last_student;

            // Analytics values
            analyticsPresentCount.textContent = stats.total_present_today;
            analyticsAbsentCount.textContent = stats.total_absent_today;
            analyticsTotalCount.textContent = stats.total_students;
            analyticsPercentage.textContent = `${stats.attendance_rate}%`;

            if (currentTab === "analytics") {
                updateAnalyticsRing();
            }

        } catch (err) {
            console.error("Error polling metrics stats:", err);
        }
    }

    function updateAnalyticsRing() {
        const percentText = analyticsPercentage.textContent;
        const percent = parseInt(percentText) || 0;
        
        // Circular ring calculation
        const radius = progressRing.r.baseVal.value;
        const circumference = radius * 2 * Math.PI;
        const offset = circumference - (percent / 100) * circumference;
        
        progressRing.style.strokeDasharray = `${circumference} ${circumference}`;
        progressRing.style.strokeDashoffset = offset;
    }

    async function pollLiveLogs() {
        if (!isCameraActive) return;

        try {
            const res = await fetch("/api/attendance");
            const data = await res.json();
            const allLogs = data.records;

            if (allLogs.length === 0) return;

            // Detect new attendance items since last check
            let newRecords = [];
            
            // Reverse loop to check chronologically (oldest to newest)
            for (let i = allLogs.length - 1; i >= 0; i--) {
                const log = allLogs[i];
                const uid = `${log.name}_${log.date}_${log.time}`;
                
                if (!knownLogIds.has(uid)) {
                    knownLogIds.add(uid);
                    newRecords.push(log);
                }
            }

            if (newRecords.length > 0) {
                // If it is the very first page load, we just populate the cache without playing notification chime
                if (initialLoad) {
                    initialLoad = false;
                    return;
                }

                // Add elements to the Session logs list
                newRecords.forEach(log => {
                    sessionLogsCount++;
                    sessionCountBadge.textContent = `${sessionLogsCount} Logs`;
                    
                    // Hide empty placeholder
                    if (liveLogsEmpty) {
                        liveLogsEmpty.style.display = "none";
                    }

                    const logEl = document.createElement("div");
                    logEl.className = "log-item";
                    
                    const safeName = escapeHtml(log.name || 'Unknown');
                    const initials = (log.name || 'Unknown').split(' ').map(n => n[0]).join('').substring(0, 2);
                    
                    logEl.innerHTML = `
                        <div class="log-left">
                            <div class="log-avatar">${escapeHtml(initials)}</div>
                            <div class="log-info">
                                <h4>${safeName}</h4>
                                <span>Time Logged: ${escapeHtml(log.time || '')}</span>
                            </div>
                        </div>
                        <div class="log-right">
                            <span class="badge">Present</span>
                        </div>
                    `;
                    
                    // Insert at top of session logs container
                    liveLogsContainer.insertBefore(logEl, liveLogsContainer.firstChild);
                    
                    // Trigger sound chime & toast notice
                    playSuccessChime();
                    showToast("Attendance Marked", `${log.name} has been marked PRESENT.`, "success");
                });

                // Update counts immediately
                updateDashboardStats();
            }

        } catch (err) {
            console.error("Error polling logs:", err);
        }
    }

    // --- Logout Handler ---
    async function handleLogout() {
        if (confirm("Are you sure you want to sign out?")) {
            try {
                await fetch("/api/logout", { method: "POST" });
                showToast("Signed Out", "You have been logged out successfully.", "success");
                setTimeout(() => {
                    window.location.href = "/login";
                }, 1000);
            } catch (err) {
                showToast("Logout Error", "Unable to sign out. Please try again.", "danger");
            }
        }
    }

    btnLogout?.addEventListener("click", handleLogout);

    // --- App Initialization ---
    async function init() {
        // Initial setup
        await checkCameraStatus();
        await fetchStudents();
        await updateDashboardStats();
        
        // Cache initial attendance logs so we don't treat them as "new marks"
        try {
            const res = await fetch("/api/attendance");
            const data = await res.json();
            data.records.forEach(log => {
                const uid = `${log.name}_${log.date}_${log.time}`;
                knownLogIds.add(uid);
            });
        } catch (e) {
            console.error("Failed to load initial records:", e);
        }

        initialLoad = false;

        // Start background polling intervals
        // Poll for dashboard metrics every 5 seconds
        setInterval(updateDashboardStats, 5000);
        
        // Poll for new live attendance records scan every 2 seconds
        setInterval(pollLiveLogs, 2000);
    }

    init();
});
