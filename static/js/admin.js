// Admin Dashboard JavaScript
document.addEventListener('DOMContentLoaded', () => {

    // ── Helpers ──────────────────────────────────────────────────────────────
    function esc(v) {
        return String(v)
            .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
            .replace(/"/g,'&quot;').replace(/'/g,'&#039;');
    }
    function fmtDate(iso) {
        if (!iso) return '—';
        return new Date(iso).toLocaleString('en-IN', { dateStyle:'medium', timeStyle:'short' });
    }
    function showToast(title, msg, type='success') {
        const tc = document.getElementById('toast-container');
        const t = document.createElement('div');
        t.className = `toast ${type}`;
        t.innerHTML = `<div class="toast-icon">${type==='success'?'✓':'✕'}</div>
            <div class="toast-content"><h4>${esc(title)}</h4><p>${esc(msg)}</p></div>`;
        tc.appendChild(t);
        setTimeout(() => { t.style.opacity='0'; setTimeout(()=>t.remove(),300); }, 4000);
    }
    async function api(url, method='GET', body=null) {
        const opts = { method, headers: { 'Content-Type':'application/json' } };
        if (body) opts.body = JSON.stringify(body);
        const r = await fetch(url, opts);
        return r.json();
    }

    // ── Date ──────────────────────────────────────────────────────────────────
    document.getElementById('current-date').textContent =
        new Date().toLocaleDateString('en-IN', { weekday:'short', year:'numeric', month:'short', day:'numeric' });

    // ── Logout ────────────────────────────────────────────────────────────────
    document.getElementById('btn-logout').addEventListener('click', async () => {
        await api('/api/logout', 'POST');
        window.location.href = '/login';
    });

    // ── Current User ──────────────────────────────────────────────────────────
    api('/api/me').then(d => {
        if (d.name) document.getElementById('admin-name').textContent = d.name;
    });

    // ── Tabs ──────────────────────────────────────────────────────────────────
    const tabTitles = {
        overview:    ['System Overview',      'Full system statistics and recent activity'],
        users:       ['User Management',      'Create, edit, and deactivate user accounts'],
        departments: ['Departments',          'Manage academic departments'],
        classes:     ['Classes',              'Manage class sections and assignments'],
        attendance:  ['Attendance Records',   'View and export all attendance data'],
        audit:       ['Audit Logs',           'Track all system activity and changes'],
    };

    document.querySelectorAll('.menu-item').forEach(item => {
        item.addEventListener('click', e => {
            e.preventDefault();
            const tab = item.dataset.tab;
            document.querySelectorAll('.menu-item').forEach(m => m.classList.remove('active'));
            item.classList.add('active');
            document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
            document.getElementById(`tab-${tab}`).classList.add('active');
            const [title, sub] = tabTitles[tab] || [tab, ''];
            document.getElementById('page-title').textContent = title;
            document.getElementById('page-subtitle').textContent = sub;
            loaders[tab]?.();
        });
    });

    // ── Loaders (called once per tab visit) ───────────────────────────────────
    const loaded = {};
    const loaders = {
        overview:    loadOverview,
        users:       loadUsers,
        departments: loadDepts,
        classes:     loadClasses,
        attendance:  loadAttendance,
        audit:       loadAudit,
    };

    // ── Overview ──────────────────────────────────────────────────────────────
    async function loadOverview() {
        try {
            const d = await api('/api/admin/dashboard');
            if (d.success) {
                document.getElementById('stat-users').textContent    = d.users.total;
                document.getElementById('stat-teachers').textContent = d.users.teachers;
                document.getElementById('stat-faculty').textContent  = d.users.faculty;
                document.getElementById('stat-students').textContent = d.users.students;
                document.getElementById('stat-depts').textContent    = d.structure.departments;
                document.getElementById('stat-classes').textContent  = d.structure.classes;
                document.getElementById('stat-subjects').textContent = d.structure.subjects;
                document.getElementById('stat-sessions').textContent = d.sessions.total;
            }
        } catch(e) { console.error(e); }

        // Recent students
        try {
            const s = await api('/api/students');
            const tbody = document.getElementById('recent-students-body');
            const recent = (s.students || []).slice(0, 8);
            if (recent.length === 0) {
                tbody.innerHTML = '<tr><td colspan="3" style="color:var(--text-muted);text-align:center;padding:20px;">No students registered yet.</td></tr>';
            } else {
                tbody.innerHTML = recent.map(st => `<tr>
                    <td style="font-weight:600;color:var(--text-primary);">${esc(st.name)}</td>
                    <td style="color:var(--text-muted);">${esc(st.registered_at)}</td>
                    <td><a href="${esc(st.photo_url)}" target="_blank" style="color:var(--accent);font-size:12px;">View photo</a></td>
                </tr>`).join('');
            }
        } catch(e) { console.error(e); }
    }

    // ── Users ─────────────────────────────────────────────────────────────────
    async function loadUsers() {
        const tbody = document.getElementById('users-tbody');
        tbody.innerHTML = '<tr><td colspan="6" style="color:var(--text-muted);text-align:center;padding:24px;">Loading...</td></tr>';
        try {
            const d = await api('/api/admin/users');
            if (!d.success) throw new Error(d.message);
            if (d.users.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" style="color:var(--text-muted);text-align:center;padding:24px;">No users found.</td></tr>';
                return;
            }
            tbody.innerHTML = d.users.map(u => `<tr>
                <td style="font-weight:600;color:var(--text-primary);">${esc(u.name)}</td>
                <td style="color:var(--text-secondary);">${esc(u.email)}</td>
                <td><span class="role-badge ${esc(u.role)}">${esc(u.role)}</span></td>
                <td><span class="status-dot ${u.is_active?'active':'inactive'}"></span>${u.is_active?'Active':'Inactive'}</td>
                <td style="color:var(--text-muted);">${fmtDate(u.last_login)}</td>
                <td>
                    <div class="action-row">
                        <button class="btn btn-secondary btn-xs btn-toggle-user" data-id="${u.id}" data-active="${u.is_active}">${u.is_active?'Deactivate':'Activate'}</button>
                        <button class="btn btn-danger btn-xs btn-delete-user" data-id="${u.id}" data-name="${esc(u.name)}">Delete</button>
                    </div>
                </td>
            </tr>`).join('');
            bindUserActions();
        } catch(e) {
            tbody.innerHTML = `<tr><td colspan="6" style="color:var(--status-absent);text-align:center;padding:24px;">${esc(e.message)}</td></tr>`;
        }
    }

    function bindUserActions() {
        document.querySelectorAll('.btn-toggle-user').forEach(btn => {
            btn.addEventListener('click', async () => {
                const id = btn.dataset.id;
                const nowActive = btn.dataset.active === 'true';
                const res = await api(`/api/admin/users/${id}`, 'PUT', { is_active: !nowActive });
                if (res.success) { showToast('User updated', res.message); loadUsers(); }
                else showToast('Error', res.message, 'danger');
            });
        });
        document.querySelectorAll('.btn-delete-user').forEach(btn => {
            btn.addEventListener('click', async () => {
                if (!confirm(`Delete user "${btn.dataset.name}"? This cannot be undone.`)) return;
                const res = await api(`/api/admin/users/${btn.dataset.id}`, 'DELETE');
                if (res.success) { showToast('Deleted', res.message); loadUsers(); }
                else showToast('Error', res.message, 'danger');
            });
        });
    }

    // New user form
    document.getElementById('btn-new-user').addEventListener('click', () => {
        document.getElementById('new-user-form-wrap').style.display = 'block';
    });
    document.getElementById('btn-cancel-user').addEventListener('click', () => {
        document.getElementById('new-user-form-wrap').style.display = 'none';
    });
    document.getElementById('btn-create-user').addEventListener('click', async () => {
        const name     = document.getElementById('new-user-name').value.trim();
        const email    = document.getElementById('new-user-email').value.trim();
        const password = document.getElementById('new-user-password').value.trim();
        const role     = document.getElementById('new-user-role').value;
        if (!name || !email || !password) { showToast('Missing fields', 'All fields are required.', 'danger'); return; }
        const res = await api('/api/admin/users', 'POST', { name, email, password, role });
        if (res.success) {
            showToast('User created', res.message);
            document.getElementById('new-user-form-wrap').style.display = 'none';
            ['new-user-name','new-user-email','new-user-password'].forEach(id => document.getElementById(id).value = '');
            loadUsers();
        } else {
            showToast('Error', res.message, 'danger');
        }
    });

    // ── Departments ───────────────────────────────────────────────────────────
    async function loadDepts() {
        const tbody = document.getElementById('depts-tbody');
        tbody.innerHTML = '<tr><td colspan="5" style="color:var(--text-muted);text-align:center;padding:24px;">Loading...</td></tr>';
        try {
            const d = await api('/api/admin/departments');
            if (!d.success) throw new Error(d.message);
            if (d.departments.length === 0) {
                tbody.innerHTML = '<tr><td colspan="5" style="color:var(--text-muted);text-align:center;padding:24px;">No departments found.</td></tr>';
                return;
            }
            tbody.innerHTML = d.departments.map(dep => `<tr>
                <td style="font-weight:600;color:var(--text-primary);">${esc(dep.name)}</td>
                <td><span class="role-badge admin">${esc(dep.code)}</span></td>
                <td style="color:var(--text-secondary);">${dep.class_count}</td>
                <td style="color:var(--text-muted);">${esc(dep.description||'—')}</td>
                <td>
                    <div class="action-row">
                        <button class="btn btn-danger btn-xs btn-delete-dept" data-id="${dep.id}" data-name="${esc(dep.name)}">Delete</button>
                    </div>
                </td>
            </tr>`).join('');
            document.querySelectorAll('.btn-delete-dept').forEach(btn => {
                btn.addEventListener('click', async () => {
                    if (!confirm(`Delete department "${btn.dataset.name}"?`)) return;
                    const res = await api(`/api/admin/departments/${btn.dataset.id}`, 'DELETE');
                    if (res.success) { showToast('Deleted', res.message); loadDepts(); }
                    else showToast('Error', res.message, 'danger');
                });
            });
        } catch(e) {
            tbody.innerHTML = `<tr><td colspan="5" style="color:var(--status-absent);text-align:center;padding:24px;">${esc(e.message)}</td></tr>`;
        }
    }

    document.getElementById('btn-new-dept').addEventListener('click', () => {
        document.getElementById('new-dept-form-wrap').style.display = 'block';
    });
    document.getElementById('btn-cancel-dept').addEventListener('click', () => {
        document.getElementById('new-dept-form-wrap').style.display = 'none';
    });
    document.getElementById('btn-create-dept').addEventListener('click', async () => {
        const name = document.getElementById('new-dept-name').value.trim();
        const code = document.getElementById('new-dept-code').value.trim();
        const desc = document.getElementById('new-dept-desc').value.trim();
        if (!name || !code) { showToast('Missing fields', 'Name and code are required.', 'danger'); return; }
        const res = await api('/api/admin/departments', 'POST', { name, code, description: desc });
        if (res.success) {
            showToast('Created', res.message);
            document.getElementById('new-dept-form-wrap').style.display = 'none';
            ['new-dept-name','new-dept-code','new-dept-desc'].forEach(id => document.getElementById(id).value='');
            loadDepts();
        } else {
            showToast('Error', res.message, 'danger');
        }
    });

    // ── Classes ───────────────────────────────────────────────────────────────
    async function loadClasses() {
        const tbody = document.getElementById('classes-tbody');
        tbody.innerHTML = '<tr><td colspan="6" style="color:var(--text-muted);text-align:center;padding:24px;">Loading...</td></tr>';
        try {
            const d = await api('/api/admin/classes');
            if (!d.success) throw new Error(d.message);
            if (d.classes.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" style="color:var(--text-muted);text-align:center;padding:24px;">No classes found.</td></tr>';
                return;
            }
            tbody.innerHTML = d.classes.map(c => `<tr>
                <td style="font-weight:600;color:var(--text-primary);">${esc(c.name)}</td>
                <td><span class="role-badge admin">${esc(c.code)}</span></td>
                <td style="color:var(--text-secondary);">${esc(c.department_name||'—')}</td>
                <td style="color:var(--text-secondary);">${c.capacity}</td>
                <td style="color:var(--text-secondary);">${c.student_count}</td>
                <td>
                    <div class="action-row">
                        <button class="btn btn-danger btn-xs btn-delete-class" data-id="${c.id}" data-name="${esc(c.name)}">Delete</button>
                    </div>
                </td>
            </tr>`).join('');
            document.querySelectorAll('.btn-delete-class').forEach(btn => {
                btn.addEventListener('click', async () => {
                    if (!confirm(`Delete class "${btn.dataset.name}"?`)) return;
                    const res = await api(`/api/admin/classes/${btn.dataset.id}`, 'DELETE');
                    if (res.success) { showToast('Deleted', res.message); loadClasses(); }
                    else showToast('Error', res.message, 'danger');
                });
            });
        } catch(e) {
            tbody.innerHTML = `<tr><td colspan="6" style="color:var(--status-absent);text-align:center;padding:24px;">${esc(e.message)}</td></tr>`;
        }
    }

    // Populate dept dropdown in class form
    async function populateDeptSelect() {
        const sel = document.getElementById('new-class-dept');
        const d = await api('/api/admin/departments');
        if (d.success) {
            d.departments.forEach(dep => {
                const opt = document.createElement('option');
                opt.value = dep.id;
                opt.textContent = `${dep.name} (${dep.code})`;
                sel.appendChild(opt);
            });
        }
    }

    document.getElementById('btn-new-class').addEventListener('click', async () => {
        const wrap = document.getElementById('new-class-form-wrap');
        wrap.style.display = 'block';
        const sel = document.getElementById('new-class-dept');
        if (sel.options.length <= 1) await populateDeptSelect();
    });
    document.getElementById('btn-cancel-class').addEventListener('click', () => {
        document.getElementById('new-class-form-wrap').style.display = 'none';
    });
    document.getElementById('btn-create-class').addEventListener('click', async () => {
        const name     = document.getElementById('new-class-name').value.trim();
        const code     = document.getElementById('new-class-code').value.trim();
        const dept_id  = parseInt(document.getElementById('new-class-dept').value);
        const capacity = parseInt(document.getElementById('new-class-capacity').value) || 60;
        if (!name || !code || !dept_id) { showToast('Missing fields', 'Name, code, and department are required.', 'danger'); return; }
        const res = await api('/api/admin/classes', 'POST', { name, code, department_id: dept_id, capacity });
        if (res.success) {
            showToast('Created', res.message);
            document.getElementById('new-class-form-wrap').style.display = 'none';
            ['new-class-name','new-class-code'].forEach(id => document.getElementById(id).value='');
            loadClasses();
        } else {
            showToast('Error', res.message, 'danger');
        }
    });

    // ── Attendance ────────────────────────────────────────────────────────────
    async function loadAttendance() {
        const tbody = document.getElementById('attendance-tbody');
        tbody.innerHTML = '<tr><td colspan="5" style="color:var(--text-muted);text-align:center;padding:24px;">Loading...</td></tr>';
        try {
            const d = await api('/api/attendance');
            const records = d.records || [];
            if (records.length === 0) {
                tbody.innerHTML = '<tr><td colspan="5" style="color:var(--text-muted);text-align:center;padding:24px;">No attendance records found.</td></tr>';
                return;
            }
            const statusColor = { Present: 'var(--status-present)', Absent: 'var(--status-absent)', Late: 'var(--status-late)' };
            tbody.innerHTML = records.slice(0,200).map((r,i) => `<tr>
                <td style="color:var(--text-muted);">${r.id}</td>
                <td style="font-weight:600;color:var(--text-primary);">${esc(r.name)}</td>
                <td style="color:var(--text-secondary);">${esc(r.date)}</td>
                <td style="color:var(--text-secondary);">${esc(r.time)}</td>
                <td><span style="color:${statusColor[r.status]||'var(--text-secondary)'};font-weight:600;">${esc(r.status)}</span></td>
            </tr>`).join('');
        } catch(e) {
            tbody.innerHTML = `<tr><td colspan="5" style="color:var(--status-absent);text-align:center;padding:24px;">Error: ${esc(e.message)}</td></tr>`;
        }
    }

    // ── Audit Logs ────────────────────────────────────────────────────────────
    async function loadAudit() {
        const list = document.getElementById('audit-list');
        list.innerHTML = '<div style="color:var(--text-muted);text-align:center;padding:32px;">Loading...</div>';
        try {
            const d = await api('/api/admin/audit-logs?limit=100');
            if (!d.success) { list.innerHTML = `<div style="color:var(--text-muted);padding:24px;">${esc(d.message||'No audit log route available')}</div>`; return; }
            const logs = d.logs || [];
            if (logs.length === 0) { list.innerHTML = '<div style="color:var(--text-muted);text-align:center;padding:32px;">No audit logs yet.</div>'; return; }
            list.innerHTML = logs.map(l => `<div class="audit-entry">
                <span class="audit-time">${fmtDate(l.timestamp)}</span>
                <span class="audit-action">${esc(l.action)}</span>
                <span style="color:var(--text-secondary);flex:1;">${esc(l.details||'')}</span>
                <span style="color:var(--text-muted);">${esc(l.user_name||'System')}</span>
            </div>`).join('');
        } catch(e) {
            list.innerHTML = `<div style="color:var(--text-muted);text-align:center;padding:24px;">Audit log endpoint not available.</div>`;
        }
    }

    // ── Theme Switcher ────────────────────────────────────────────────────────
    const themeBtn = document.getElementById('btn-theme-toggle');
    const themeLabel = document.getElementById('theme-label');
    
    function applyTheme(theme) {
        if (theme === 'light') {
            document.documentElement.classList.remove('dark-theme');
            document.documentElement.classList.add('light-theme');
            if (themeLabel) themeLabel.textContent = 'Light';
            localStorage.setItem('appTheme', 'light');
        } else {
            document.documentElement.classList.remove('light-theme');
            document.documentElement.classList.add('dark-theme');
            if (themeLabel) themeLabel.textContent = 'Dark';
            localStorage.setItem('appTheme', 'dark');
        }
    }
    
    applyTheme(localStorage.getItem('appTheme') || 'dark');
    
    if (themeBtn) {
        themeBtn.addEventListener('click', () => {
            const isDark = document.documentElement.classList.contains('dark-theme');
            applyTheme(isDark ? 'light' : 'dark');
        });
    }

    // ── Boot: load first tab ──────────────────────────────────────────────────
    loadOverview();
});
