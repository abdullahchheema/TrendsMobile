FROM frappe/erpnext:v16.52.1
COPY --chown=frappe:frappe dist/covison_erp-1.0.0-py3-none-any.whl /tmp/covison_erp-1.0.0-py3-none-any.whl
RUN /home/frappe/frappe-bench/env/bin/pip install --no-deps /tmp/covison_erp-1.0.0-py3-none-any.whl
RUN ln -s /home/frappe/frappe-bench/env/lib/python3.14/site-packages/covison_erp /home/frappe/frappe-bench/apps/covison_erp
RUN ln -s /home/frappe/frappe-bench/env/lib/python3.14/site-packages/covison_erp/public /home/frappe/frappe-bench/assets/covison_erp
