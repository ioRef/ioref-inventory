// Make changelist rows clickable.
//
// The link is still a real <a> in the first cell -- this only widens its hit
// area to the whole row. Keyboard users tab to the link exactly as before, and
// screen readers see unchanged markup, which is why the row is not given a
// role="link" or a tabindex of its own.
(function () {
   "use strict";

   function rowTarget(row) {
      // The changelist's own link to the change form. field-<name> cells hold
      // the display columns; the first anchor among them is the object link.
      var link = row.querySelector("th a[href], td a[href]");
      return link && link.getAttribute("href");
   }

   function isInteractive(el) {
      return el.closest("a, button, input, select, textarea, label, .action-checkbox");
   }

   function init() {
      var rows = document.querySelectorAll("#result_list tbody tr");

      rows.forEach(function (row) {
         var href = rowTarget(row);
         if (!href) {
            return;
         }

         row.style.cursor = "pointer";

         row.addEventListener("click", function (event) {
            // Let real controls behave normally: the select checkbox, action
            // links, and any anchor already in the row.
            if (isInteractive(event.target)) {
               return;
            }

            // Selecting text in a cell should not navigate away.
            var selection = window.getSelection();
            if (selection && selection.toString().length > 0) {
               return;
            }

            // Preserve the modifier conventions people expect from a link.
            if (event.metaKey || event.ctrlKey || event.shiftKey) {
               window.open(href, "_blank", "noopener");
            } else {
               window.location.href = href;
            }
         });

         // Middle-click opens in a new tab, as it would on the anchor itself.
         row.addEventListener("auxclick", function (event) {
            if (event.button === 1 && !isInteractive(event.target)) {
               event.preventDefault();
               window.open(href, "_blank", "noopener");
            }
         });
      });
   }

   if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", init);
   } else {
      init();
   }
})();
