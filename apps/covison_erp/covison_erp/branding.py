"""Apply product branding through supported settings and boot hooks."""

import frappe


def boot_session(boot):
    boot.home_page = "mobile-shop"
    boot.covison = {"title": "Covison ERP", "currency": "PKR", "version": "1.0.0"}
    for app in boot.get("app_data", []):
        if app.get("app_name") == "erpnext":
            app.update(app_title="Covison ERP", app_logo_url="/assets/covison_erp/images/covison-mark.svg",
                       app_route="/desk/mobile-shop")
        elif app.get("app_name") == "frappe":
            app.update(app_title="Administration")
