// ธีม + sidebar: ค่าตั้งต้นถูกใส่ไว้ใน <head> แล้ว (กันหน้ากระพริบ) ไฟล์นี้จัดการการกดปุ่ม
(function () {
  const root = document.documentElement;

  function store(key, value) {
    try { localStorage.setItem(key, value); } catch (e) {}
  }

  function syncThemeIcon() {
    const dark = root.dataset.theme === "dark";
    document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
      btn.querySelector(".ph").className = "ph " + (dark ? "ph-sun" : "ph-moon");
      btn.setAttribute("aria-label", dark ? "เปลี่ยนเป็นโหมดสว่าง" : "เปลี่ยนเป็นโหมดมืด");
      btn.title = btn.getAttribute("aria-label");
    });
  }

  document.addEventListener("click", (event) => {
    const el = event.target.closest("[data-theme-toggle],[data-sidebar-toggle],[data-nav-open],[data-nav-close],[data-reveal]");
    if (!el) return;

    if (el.matches("[data-theme-toggle]")) {
      root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
      store("theme", root.dataset.theme);
      syncThemeIcon();
    } else if (el.matches("[data-sidebar-toggle]")) {
      const collapsed = root.dataset.sidebar !== "collapsed";
      root.dataset.sidebar = collapsed ? "collapsed" : "expanded";
      store("sidebar", root.dataset.sidebar);
    } else if (el.matches("[data-nav-open]")) {
      document.body.classList.add("nav-open");
    } else if (el.matches("[data-nav-close]")) {
      document.body.classList.remove("nav-open");
    } else if (el.matches("[data-reveal]")) {
      const input = document.getElementById(el.dataset.reveal);
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      el.querySelector(".ph").className = "ph " + (show ? "ph-eye-slash" : "ph-eye");
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      document.body.classList.remove("nav-open");
      document.querySelectorAll("details.dropdown[open]").forEach((d) => d.removeAttribute("open"));
    }
  });

  // ปิด dropdown เมื่อคลิกข้างนอก
  document.addEventListener("click", (event) => {
    document.querySelectorAll("details.dropdown[open]").forEach((d) => {
      if (!d.contains(event.target)) d.removeAttribute("open");
    });
  });

  // ข้อความบน dropdown หลายตัวเลือก
  document.querySelectorAll("details.dropdown").forEach((d) => {
    const label = d.querySelector("summary span[data-empty]");
    if (!label) return;
    const empty = label.dataset.empty;
    const update = () => {
      const picked = [...d.querySelectorAll("input:checked")].map((i) => i.dataset.label);
      label.textContent = picked.length === 0 ? empty
        : picked.length === 1 ? picked[0] : `เลือก ${picked.length} รายการ`;
    };
    d.addEventListener("change", update);
    update();
  });

  // ช่วงเวลา: เลือกช่วงสำเร็จรูป -> เติมวันที่ให้, แก้วันที่เอง -> เปลี่ยนเป็น "กำหนดเอง"
  document.querySelectorAll("[data-period]").forEach((select) => {
    const form = select.form;
    const start = form.querySelector('[data-date="start"]');
    const end = form.querySelector('[data-date="end"]');
    const hint = form.querySelector("[data-thai-range]");
    const thai = (iso) => {
      const [y, m, d] = iso.split("-");
      return `${d}/${m}/${+y + 543}`;
    };
    const sync = () => {
      start.max = end.value;
      end.min = start.value;
      if (!start.value || !end.value) return;
      const days = Math.round((new Date(end.value) - new Date(start.value)) / 86400000) + 1;
      hint.textContent = days > 0
        ? `${thai(start.value)} – ${thai(end.value)} · ${days} วัน`
        : "วันเริ่มต้องไม่เกินวันสิ้นสุด";
    };
    select.addEventListener("change", () => {
      const opt = select.selectedOptions[0];
      if (opt.dataset.start) {
        start.value = opt.dataset.start;
        end.value = opt.dataset.end;
      }
      sync();
    });
    [start, end].forEach((input) => input.addEventListener("input", () => {
      select.value = "custom";
      sync();
    }));
    sync();
  });

  // ปุ่มที่ส่งฟอร์ม: แสดงสถานะกำลังทำงาน
  document.querySelectorAll("form[data-busy]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      const btn = event.submitter;
      if (!btn) return;
      setTimeout(() => {
        if (event.defaultPrevented) return;  // ผู้ใช้กดยกเลิกใน confirm
        btn.disabled = true;
        const icon = btn.querySelector(".ph");
        if (icon) icon.className = "ph ph-circle-notch spin";
      });
    });
  });

  // สถานะการเชื่อมต่อใน sidebar
  const conn = document.querySelector("[data-conn]");
  if (conn) {
    fetch("/api/status").then((r) => r.json()).then((s) => {
      conn.querySelector(".dot").className = "dot " + (s.ok ? "ok" : "err");
      conn.querySelector("[data-conn-msg]").textContent = s.ok ? "เชื่อมต่อแล้ว" : "เชื่อมต่อไม่ได้";
      conn.title = s.message;
    }).catch(() => {});
  }

  // ยืนยันก่อนส่งฟอร์มที่ย้อนกลับไม่ได้ (data-confirm="ข้อความ")
  document.addEventListener("submit", (event) => {
    const msg = event.target.dataset.confirm;
    if (msg && !confirm(msg)) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);

  // ฟอร์มบันทึกการฉีด: เลือกวัคซีน -> เสนอ lot ที่ใช้ล่าสุด, ฉีดที่นี่ต้องมี lot
  document.querySelectorAll("[data-vaccination-form]").forEach((form) => {
    const code = form.querySelector('[name="vaccine_code"]');
    const lots = form.querySelector("[data-lots]");
    const place = form.querySelectorAll('[name="place"]');
    const lot = form.querySelector('[name="lot_no"]');
    const syncPlace = () => {
      const here = form.querySelector('[name="place"]:checked')?.value === "here";
      lot.required = here;
      form.querySelectorAll("[data-here-only]").forEach((el) => (el.hidden = !here));
      form.querySelectorAll("[data-away-only]").forEach((el) => (el.hidden = here));
    };
    place.forEach((r) => r.addEventListener("change", syncPlace));
    syncPlace();
    const loadLots = () => {
      lots.innerHTML = "";
      if (!code.value) return;
      fetch("/api/lots?code=" + encodeURIComponent(code.value)).then((r) => r.json()).then((rows) => {
        rows.forEach((row) => {
          const b = document.createElement("button");
          b.type = "button";
          b.className = "chip";
          b.textContent = "lot " + row.lot_no + (row.expire_date ? " · หมดอายุ " + row.expire_date : "");
          b.addEventListener("click", () => {
            lot.value = row.lot_no;
            form.querySelector('[name="expire_date"]').value = row.expire_date;
            form.querySelector('[name="manufacturer"]').value = row.manufacturer;
          });
          lots.append(b);
        });
      }).catch(() => {});
    };
    code.addEventListener("change", loadLots);
    loadLots();
  });

  // แถบแจ้งเวอร์ชันใหม่ (โปรแกรมเช็กตอนเปิด ใช้เวลาสักครู่ จึงลองถามซ้ำ 2-3 ครั้ง)
  const banner = document.querySelector("[data-update-banner]");
  if (banner) {
    const showUpdate = (s) => {
      if (!s.available || s.dismissed) return false;
      banner.querySelector("[data-update-version]").textContent = s.latest;
      banner.querySelector("[data-update-date]").textContent = s.date ? "· " + s.date : "";
      banner.querySelector("[data-update-notes]").textContent = s.notes || "-";
      if (!s.can_install) {
        banner.querySelector("[data-update-install]").outerHTML =
          '<span class="muted small">อัปเดตได้เฉพาะตอนใช้งานจาก EPITrack.exe</span>';
      }
      banner.hidden = false;
      return true;
    };
    let tries = 0;
    const ask = () => fetch("/api/update").then((r) => r.json()).then((s) => {
      if (!showUpdate(s) && !s.checked && ++tries < 4) setTimeout(ask, 3000);
    }).catch(() => {});
    ask();
    banner.querySelector("[data-update-later]").addEventListener("click", () => {
      banner.hidden = true;
      fetch("/update/dismiss", { method: "POST" });
    });
    banner.querySelector("[data-update-install]")?.addEventListener("submit", (event) => {
      if (!confirm("โปรแกรมจะปิดแล้วเปิดใหม่เพื่ออัปเดต งานที่ยังไม่ได้บันทึกจะหายไป ต้องการอัปเดตเลยหรือไม่?")) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    }, true);
  }

  syncThemeIcon();
})();
