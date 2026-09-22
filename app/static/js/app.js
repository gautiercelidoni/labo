/* Comportements légers : CSRF pour HTMX, confirmations, soumission automatique des filtres. */
(function () {
  "use strict";

  var tokenMeta = document.querySelector('meta[name="csrf-token"]');

  // Jeton CSRF ajouté à toutes les requêtes HTMX (POST inclus).
  document.addEventListener("htmx:configRequest", function (event) {
    if (tokenMeta) {
      event.detail.headers["X-CSRFToken"] = tokenMeta.getAttribute("content");
    }
  });

  // Confirmation avant les actions sensibles : <form data-confirm="Message">.
  document.addEventListener("submit", function (event) {
    var form = event.target;
    var message = form.getAttribute("data-confirm");
    if (message && !window.confirm(message)) {
      event.preventDefault();
    }
  });

  // Filtres : les listes déroulantes marquées data-autosubmit soumettent leur formulaire.
  document.addEventListener("change", function (event) {
    var el = event.target;
    if (el.matches && el.matches("[data-autosubmit]") && el.form) {
      el.form.submit();
    }
  });

  // Chargement différé du script des graphiques, seulement si la page en contient.
  document.addEventListener("DOMContentLoaded", function () {
    if (!document.querySelector("[data-chart-url]")) {
      return;
    }
    var vendor = document.createElement("script");
    vendor.src = document.body.getAttribute("data-chartjs") || "/static/vendor/chart.umd.min.js";
    vendor.onload = function () {
      var charts = document.createElement("script");
      charts.src = "/static/js/charts.js";
      document.body.appendChild(charts);
    };
    document.body.appendChild(vendor);
  });
})();
