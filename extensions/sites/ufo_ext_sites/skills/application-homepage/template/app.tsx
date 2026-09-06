import { mountApp, Page, Header } from "ufo/kit";

function App() {
  return (
    <Page>
      <div data-app-region="overview">
        <Header>Application source is not written yet.</Header>
      </div>
    </Page>
  );
}

mountApp(document.getElementById("root")!, () => <App />);
