
function validar_dia() {
  const dia = document.getElementById("dia").value;
  const especie = document.getElementById("especie").value;
  const obs = document.getElementById("obs").value;
  const msgErro = document.querySelector(".msg-erro");

  msgErro.textContent = "";
  msgErro.classList.add("oculto");

  if (!dia || !especie) {
    msgErro.textContent = "Defina um dia e selecione a espécie do animal para continuar.";
    msgErro.classList.remove("oculto");
    return;
  }

  fetch("../api/v1/consultar-dia", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dia, especie, obs })
  })
    .then(async (res) => {
      const isJson = res.headers.get("content-type")?.includes("application/json");
      const data = isJson ? await res.json().catch(() => ({})) : {};
      if (!res.ok) {
        throw new Error(data.mensagem || `Erro HTTP ${res.status}`);
      }
      return data;
    })
    .then((data) => {
      if (data.status === "ok") {
        document.querySelector("form").submit();
      } else {
        msgErro.textContent = data.mensagem || "Erro ao validar.";
        msgErro.classList.remove("oculto");
      }
    })
    .catch((err) => {
      msgErro.textContent = err.message || "Erro de conexão com o servidor.";
      msgErro.classList.remove("oculto");
    })
}
