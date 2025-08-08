function getLiturgicalDay() {
  return new Date().toLocaleDateString('no-NO', { weekday: 'long' }).toLowerCase(); // e.g., 'mandag'
}

async function loadCompletorium(day) {
  const section = document.getElementById("completorium");

  try {
    const response = await fetch(`completorium/${day}.json`);
    const data = await response.json();

    const fixedParts = {
      innledning: `
      <h3>Innledning</h3>
      <p><span class="response">℣:</span> Gud, kom meg til hjelp.<br>
         <span class="response">℟:</span> Herre, vær snar til frelse.</p>
      <p><span class="response">℣:</span> Ære være Faderen og Sønnen og den Hellige Ånd,<br>
         <span class="response">℟:</span> som det var i opphavet, så nå og alltid og i all evighet. Amen. (Halleluja)</p>
      <p style="font-style:italic; color:#444;">(Kort stillhet med samvittighetsransakelse)</p>
      <p><span class="response">℟:</span> Jeg bekjenner for Gud, Den Allmektige, og for dere alle, at jeg har syndet meget i tanker og ord, gjerninger og forsømmelser ved min skyld. Derfor ber jeg den salige jomfru Maria, alle engler og hellige og dere alle: be for meg til Herren, vår Gud.</p>
      <p><span class="response">℣:</span> Den allmektige Gud miskunne seg over oss, tilgi våre synder og føre oss til det evige liv.<br>
         <span class="response">℟:</span> Amen.</p>`,
      responsorium: "℟ I dine hender, Herre, overgir jeg min ånd.",
      simeon: "Nå, Herre, lar du din tjener fare herfra i fred, slik du har lovet, for mine øyne har sett din frelse, som du har gjort i stand for alle folks åsyn, et lys til åpenbaring for hedningene og ditt folk Israel til ære.",
      velsignelse: "Herren være med oss i natt, og bevare oss fra alt ondt. Amen.",
      mariaAntifon: `
        <strong>Salve Regina</strong><br>
        Vær hilset, dronning, barmhjertighetens mor,<br>
        vårt liv, vår sødme og vårt håp, vær hilset!<br>
        Til deg roper vi, forviste Evas barn;<br>
        til deg sukker vi, sørgende og gråtende<br>
        i denne tåredal.<br>
        Så vend da dine barmhjertige øyne mot oss,<br>
        og vis oss etter dette landflyktige livet<br>
        Jesus, ditt livs velsignede frukt.<br>
        O milde, o fromme, o søte jomfru Maria.
        <br><br>
        <strong>Sub tuum praesidium</strong><br>
        Under ditt vern tar vi vår tilflukt, hellige Guds Mor.<br>
        Forakt ikke våre bønner i vår nød,<br>
        men fri oss alltid fra alle farer,<br>
        du ærverdige og velsignede Jomfru.
      `
    };

    let html = '';
    const innledning = fixedParts.innledning;
    html += innledning;

    html += `<h3>Salmer</h3>`;
    data.salmer.forEach((salme) => {
      html += `<p class="response">${salme.referanse}</p>`;
      html += `<p><em class="response">Ant. </em> ${salme.antifon}</p>`;
      html += "<p>" + salme.tekst.map(vers => `${vers}`).join("<br>") + "</p>";
      html += `<p><em class="response">Ant. </em> ${salme.antifon}</p><hr>`;
    });

    html += `<h3>Lesning</h3>`;
    html += `<p class="response">${data.lesning.referanse}</p><p>${data.lesning.tekst}</p>`;

    html += `
<h3>Responsorium</h3>
<p>
<span class="response">℣</span> I dine hender, Herre, overgir jeg min ånd.<br>
<span class="response">℟</span> I dine hender, Herre, overgir jeg min ånd.<br>
<span class="response">℣</span> Du har løskjøpt oss, Herre, sannhetens Gud.<br>
<span class="response">℟</span> I dine hender, Herre, overgir jeg min ånd.<br>
<span class="response">℣</span> Ære være Faderen og Sønnen og den Hellige Ånd.<br>
<span class="response">℟</span> I dine hender, Herre, overgir jeg min ånd.</p>

<h3>Simeons lovsang</h3>
<p><em class="response">Ant.</em> Frels oss, Herre, vokt oss om vi våker eller sover, så vi kan våke med Kristus og hvile i fred.</p>
<p>Herre, nå kan du la din tjener fare i fred, etter ditt ord.<br>
For mine øyne har sett din frelse<br>
som du har beredt for folkenes åsyn,<br>
et lys til åpenbaring for hedningene,<br>
en herlighet for ditt folk, Israel.<br>
<span style="color:#888;">Ære være...</span></p>
<p><em class="response">Ant.</em> Frels oss, Herre, vokt oss om vi våker eller sover, så vi kan våke med Kristus og hvile i fred.</p>

<h3>Bønn</h3>
<p><span class="response">℣</span> La oss be.<br>
<span class="response">℟</span> Den allmektige og barmhjertige Gud unne oss en rolig natt og en salig død. Amen.</p>

<h3>Velsignelsen</h3>
<p><span class="response">℣</span> Herren velsigne oss, bevare oss fra alt ondt og føre oss til det evige liv.<br>
<span class="response">℟</span> Amen.</p>
<h3>Maria Antifon</h3><p>${fixedParts.mariaAntifon}</p>`;

    section.innerHTML = html;
  } catch (error) {
    section.innerHTML = `<p style="color:darkred;">❌ Kunne ikke laste komplettorium for ${day}. Fil mangler eller er feilformatert.</p>`;
  }
}

function showHour(hour) {
  const allSections = document.querySelectorAll('.hour-section');
  const allButtons = document.querySelectorAll('button[data-id]');

  allSections.forEach(el => el.classList.remove('active'));
  allButtons.forEach(btn => btn.disabled = true);

  const activeSection = document.getElementById(hour);
  const activeButton = document.querySelector(`button[data-id="${hour}"]`);

  if (activeSection && activeButton) {
    activeSection.classList.add('active');
    activeButton.disabled = false;
    activeButton.classList.add('selected');
  }

  const day = getLiturgicalDay();

  if (hour === "completorium") {
    loadCompletorium(day);
  }
  // Aquí podrías añadir otros como:
  // else if (hour === "laudes") { loadLaudes(day); }
}

window.onload = () => showHour('completorium');