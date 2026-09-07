{{ fullname | escape | underline }}

.. automodule:: {{ fullname }}
   :members:
   :undoc-members:
   :show-inheritance:
   :member-order: bysource

{% block modules %}
{% if modules %}
.. rubric:: Submodules

.. autosummary::
   :toctree:
   :template: module.rst
   :recursive:
{% for item in modules %}
   {{ item }}
{%- endfor %}
{% endif %}
{% endblock %}
