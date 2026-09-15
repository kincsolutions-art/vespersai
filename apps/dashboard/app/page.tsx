import Image from "next/image";
const features = [
  ["01", "Your account", "Verified email and private access."],
  ["02", "Your model", "Connect your Nebius key and choose a supported model."],
  ["03", "Your Telegram", "Link your private chat with Vespers."],
  ["04", "Your apps", "Connect the tools you already use."],
];
export default function Home() {
  return (
    <main>
      <header>
        <Image
          src="/brand/vespers-app-dark-192.png"
          alt=""
          width={40}
          height={40}
        />
        <span>vespers</span>
        <span className="badge">In development</span>
      </header>
      <section className="intro">
        <p className="eyebrow">A little less to keep track of</p>
        <h1>
          Your day, with
          <br />
          room to breathe.
        </h1>
        <p>
          One assistant in Telegram. Connected to your apps, ready to help with
          the everyday.
        </p>
      </section>
      <section aria-labelledby="setup">
        <div className="section-heading">
          <h2 id="setup">Make it yours</h2>
          <span>Setup coming soon</span>
        </div>
        <div className="grid">
          {features.map(([number, title, description]) => (
            <article key={number}>
              <span className="number">{number}</span>
              <h3>{title}</h3>
              <p>{description}</p>
            </article>
          ))}
        </div>
      </section>
      <footer>
        This dashboard will manage your account, connections, and automations.
        Conversations happen in Telegram.
        <br />
        Scheduled tasks and suggestions also use your Nebius inference credits.
      </footer>
    </main>
  );
}
