fetch('ordo.json')
  .then(response => response.json())
  .then(data => {
    const today = new Date();
    const isoDate = today.toISOString().split("T")[0];
    const entry = data[isoDate];

    const liturgicDay = document.getElementById('liturgicDay');
    const psalmWeek = document.getElementById('psalmWeek');

    const norwegianDate = today.toLocaleDateString('no-NO', {
      day: 'numeric', month: 'long', year: 'numeric'
    });

    const norwegianWeekday = today.toLocaleDateString('no-NO', {
      weekday: 'long'
    });

    if (entry) {
      const seasonMap = {
        "lent": ["fastetiden", "Tempus Quadragesimæ"],
        "easter": ["påsketiden", "Tempus Paschale"],
        "ordinary": ["det alminnelige kirkeår", "Tempus per Annum"],
        "advent": ["advent", "Tempus Adventus"],
        "christmas": ["juletiden", "Tempus Nativitatis"]
      };

      const [norSeason, latinSeason] = seasonMap[entry.season] || ["Kirkeåret", "Tempus Liturgicum"];
      const weekdayLatin = getLatinWeekday(today.getDay());

      const liturgicalLine = `${norwegianDate}<br>${capitalize(norwegianWeekday)} i uke ${entry.week} av ${norSeason}`;
      liturgicDay.innerHTML = liturgicalLine;
      liturgicDay.title = `${weekdayLatin} – ${latinSeason}`;

      psalmWeek.textContent = `Tidebønn for uke ${entry.week}, vol. ${entry.volume}`;
      psalmWeek.title = `Hebdomada Psalmorum ${entry.week}, Volumen ${entry.volume}`;

      const liturgicalColorMap = {
        "lent": "#800000", "easter": "#ffd700", "ordinary": "#006400",
        "advent": "#4b0082", "christmas": "#fff8dc", "solemnity": "#ffffff",
        "feast": "#ff0000", "memorial": "#cc6699", "church_feast": "#ff6600"
      };

      let liturgicalColor = liturgicalColorMap[entry.season];
      if (entry.feast_type && liturgicalColorMap[entry.feast_type]) {
        liturgicalColor = liturgicalColorMap[entry.feast_type];
      }

      const colorBox = document.getElementById('colorBox');
      if (colorBox && liturgicalColor) {
        colorBox.style.backgroundColor = liturgicalColor;
        colorBox.title = `Farge: ${capitalize(entry.season)} (${entry.feast_type || 'vanlig dag'})`;
      }

      // Insert fixed Kompletorium parts
      const completoriumDiv = document.getElementById("completorium");
      const intro = `<h2>Innledning</h2><p><span class="versicle">℣.</span> Gud, kom meg til hjelp.<br><span class="response">℟.</span> Herre, vær snar til frelse.<br><br><span style="font-weight: bold;">Ære være Faderen og Sønnen og den Hellige Ånd,<br>som det var i opphavet, så nå og alltid og i all evighet. Amen.(Halleluja) </span></p>`;
      completoriumDiv.innerHTML = intro;

      // Load hymn and Marian antiphon
      fetch("kompletorium_texts.json")
        .then(res => res.json())
        .then(compData => {
          const hymns = compData.hymns || [];
          const hymn = hymns[Math.floor(Math.random() * hymns.length)];

          if (hymn) {
            const latinLines = hymn.text.filter((_, i) => i % 2 === 0);
            const norwegianLines = hymn.text.filter((_, i) => i % 2 === 1);
            let rows = "";
            for (let i = 0; i < Math.max(latinLines.length, norwegianLines.length); i++) {
              const lat = latinLines[i] || "";
              const nor = norwegianLines[i] || "";
              rows += `<tr><td>${lat}</td><td>${nor}</td></tr>`;
            }
            completoriumDiv.innerHTML += `
              <h2>Hymnus</h2>
              <table style='width:100%; border-collapse: collapse;'>
                <thead>
                  <tr>
                    <th style='text-align:left; border-bottom: 1px solid #ccc;'>Latin</th>
                    <th style='text-align:left; border-bottom: 1px solid #ccc;'>Norsk</th>
                  </tr>
                </thead>
                <tbody>
                  ${rows}
                </tbody>
              </table>
            `;
          }

          completoriumDiv.innerHTML += `<h2>Velsignelse</h2><p>Den allmektige og barmhjertige Gud unne oss en rolig natt og en salig død. Amen.</p>`;

          fetch("antifoner_jomfru_maria.json")
            .then(res => res.json())
            .then(antData => {
              const seasonMap = {
                "advent": "advent",
                "christmas": "christmas",
                "lent": "lent",
                "easter": "easter",
                "ordinary": "ordinary_time"
              };
              const seasonKey = seasonMap[entry.season] || "ordinary_time";
              const antiphon = antData.antifon_til_jomfru_maria.find(a => a.season.includes(seasonKey));
              if (antiphon) {
                completoriumDiv.innerHTML += `
                  <hr>
                  <h3>${antiphon.title}</h3>
                  <p><strong>Latin:</strong><br>${antiphon.latin.replace(/\n/g, "<br>")}</p>
                  <p><strong>Norsk:</strong><br>${antiphon.norwegian.replace(/\n/g, "<br>")}</p>
                `;
              }
            });
        });
    } else {
      liturgicDay.textContent = norwegianDate;
      psalmWeek.textContent = "Ingen informasjon for i dag.";
    }
  })
  .catch(err => {
    console.error("Feil ved lasting av ordo:", err);
    document.getElementById('liturgicDay').textContent = "Ugyldig dato.";
    document.getElementById('psalmWeek').textContent = "Feil ved lasting av dagens ordo.";
  });

function capitalize(str) {
  return str.charAt(0).toUpperCase() + str.slice(1);
}

function getLatinWeekday(weekdayNo) {
  const feria = {
    0: "Dominica", 1: "Feria Secunda", 2: "Feria Tertia",
    3: "Feria Quarta", 4: "Feria Quinta", 5: "Feria Sexta", 6: "Sabbatum"
  };
  return feria[weekdayNo];
}

function showHour(id) {
  const sections = document.querySelectorAll('.hour-section');
  sections.forEach(el => el.classList.remove('active'));
  document.getElementById(id).classList.add('active');

  const buttons = document.querySelectorAll('button');
  buttons.forEach(btn => btn.classList.remove('selected'));
  const clickedButton = Array.from(buttons).find(b => b.getAttribute('onclick').includes(id));
  if (clickedButton) clickedButton.classList.add('selected');
}

window.onload = () => showHour('completorium');
