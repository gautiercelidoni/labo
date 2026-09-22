/* Graphiques Levey-Jennings (Westgard) et cartes de contrôle (Shewhart), chargés à la demande. */
(function () {
  "use strict";
  if (typeof Chart === "undefined") { return; }

  var COLORS = { accepted: "#1f5fa8", warning: "#d08a00", rejected: "#b3261e" };

  // Traits verticaux aux changements de lot (plein) et de jeu de limites (pointillé).
  var changeMarkers = {
    id: "changeMarkers",
    afterDatasetsDraw: function (chart) {
      var points = chart.$labqPoints || [];
      var ctx = chart.ctx;
      var area = chart.chartArea;
      var xScale = chart.scales.x;
      ctx.save();
      points.forEach(function (p, i) {
        if (!p.lot_change && !p.limits_change) { return; }
        var x = (xScale.getPixelForValue(i) + xScale.getPixelForValue(Math.max(i - 1, 0))) / 2;
        ctx.strokeStyle = p.lot_change ? "#2d7a2d" : "#7a4fa3";
        ctx.setLineDash(p.lot_change ? [] : [4, 3]);
        ctx.beginPath();
        ctx.moveTo(x, area.top);
        ctx.lineTo(x, area.bottom);
        ctx.stroke();
        ctx.fillStyle = ctx.strokeStyle;
        ctx.fillText(p.lot_change ? "lot " + p.lot : "limites", x + 3, area.top + 10);
      });
      ctx.restore();
    }
  };

  function limitLine(points, k, label, color, dash) {
    return {
      label: label,
      data: points.map(function (p) { return p.mean + k * p.sd; }),
      borderColor: color, borderWidth: 1, borderDash: dash, pointRadius: 0, stepped: "middle", fill: false
    };
  }

  function draw(canvas, data) {
    var points = data.points;
    var empty = canvas.parentElement.querySelector(".chart-empty");
    if (!points.length) {
      if (empty) { empty.hidden = false; }
      canvas.hidden = true;
      return;
    }
    var shewhart = data.mode === "shewhart";
    var datasets = [
      {
        label: data.level + " (" + data.unit + ")",
        data: points.map(function (p) { return p.value; }),
        borderColor: "#5a6f8a", borderWidth: 1,
        pointBackgroundColor: points.map(function (p) { return COLORS[p.status]; }),
        pointBorderColor: points.map(function (p) { return COLORS[p.status]; }),
        pointRadius: points.map(function (p) { return p.status === "accepted" ? 3 : 6; }),
        pointStyle: points.map(function (p) { return p.status === "rejected" ? "crossRot" : "circle"; }),
        fill: false, order: 0
      },
      limitLine(points, 0, shewhart ? "Moyenne" : "Cible", "#222", []),
      limitLine(points, 1, "+1s", "#9aa9bd", [2, 3]),
      limitLine(points, -1, "−1s", "#9aa9bd", [2, 3]),
      limitLine(points, 2, shewhart ? "Surveillance +2s" : "+2s", "#d08a00", [6, 3]),
      limitLine(points, -2, shewhart ? "Surveillance −2s" : "−2s", "#d08a00", [6, 3]),
      limitLine(points, 3, shewhart ? "Action +3s" : "+3s", "#b3261e", [6, 3]),
      limitLine(points, -3, shewhart ? "Action −3s" : "−3s", "#b3261e", [6, 3])
    ];
    var chart = new Chart(canvas, {
      type: "line",
      data: { labels: points.map(function (p) { return p.date; }), datasets: datasets },
      plugins: [changeMarkers],
      options: {
        animation: false, maintainAspectRatio: false, responsive: true,
        interaction: { mode: "nearest", intersect: true },
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 12 } },
          tooltip: {
            filter: function (item) { return item.datasetIndex === 0; },
            callbacks: {
              title: function (items) { return points[items[0].dataIndex].date; },
              label: function (item) {
                var p = points[item.dataIndex];
                var lines = [
                  "Valeur : " + p.value.toFixed(data.decimals) + " " + data.unit,
                  "z-score : " + p.z.toFixed(2),
                  "Cible " + p.mean.toFixed(data.decimals) + " ; s " + p.sd.toFixed(data.decimals),
                  "Lot : " + p.lot,
                  "Statut : " + ({ accepted: "accepté", warning: "alerte", rejected: "rejet" })[p.status]
                ];
                if (p.rules.length) { lines.push("Règles : " + p.rules.join(", ")); }
                if (p.comment) { lines.push("Commentaire : " + p.comment); }
                return lines;
              }
            }
          }
        },
        scales: { x: { ticks: { maxRotation: 60, autoSkip: true, maxTicksLimit: 20 } } },
        onClick: function (evt, elements) {
          if (elements.length && elements[0].datasetIndex === 0) {
            window.location.href = "/ciq/series/" + points[elements[0].index].run_id;
          }
        }
      }
    });
    chart.$labqPoints = points;
    chart.update();
  }

  function load(canvas) {
    fetch(canvas.getAttribute("data-chart-url"), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) { draw(canvas, data); })
      .catch(function () { canvas.insertAdjacentText("afterend", "Impossible de charger le graphique."); });
  }

  var canvases = document.querySelectorAll("canvas[data-chart-url]");
  if ("IntersectionObserver" in window) {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) { observer.unobserve(entry.target); load(entry.target); }
      });
    }, { rootMargin: "200px" });
    canvases.forEach(function (c) { observer.observe(c); });
  } else {
    canvases.forEach(load);
  }
})();
