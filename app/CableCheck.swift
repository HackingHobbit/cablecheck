import SwiftUI

// MARK: - Data from cablecheck.py --json

struct PortReport: Codable, Identifiable {
    let number: Int
    let connected: Bool
    let mode: String          // empty, loop, device, charger, nodata
    let headline: String
    let verdict: String
    let fields: [[String]]
    let notes: [String]
    let loop_partner_reads_cable: Bool
    let has_chip: Bool
    let raw: String
    var id: Int { number }
}

struct Snapshot: Codable { let ports: [PortReport] }

@MainActor
final class Model: ObservableObject {
    @Published var ports: [PortReport] = []
    @Published var error: String?
    @Published var updated = Date()
    @Published var showRaw = false
    private var timer: Timer?
    private var running = false

    init() {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    func refresh() {
        guard !running else { return }
        running = true
        Task.detached {
            let result = Model.runChecker()
            await MainActor.run {
                self.running = false
                switch result {
                case .success(let snap):
                    self.ports = snap.ports
                    self.error = nil
                    self.updated = Date()
                case .failure(let err):
                    self.error = err.localizedDescription
                }
            }
        }
    }

    nonisolated static func runChecker() -> Result<Snapshot, Error> {
        guard let script = Bundle.main.path(forResource: "cablecheck", ofType: "py") else {
            return .failure(NSError(domain: "CableCheck", code: 1,
                                    userInfo: [NSLocalizedDescriptionKey: "cablecheck.py is missing from the app"]))
        }
        let python = ["/usr/bin/python3", "/opt/homebrew/bin/python3"]
            .first { FileManager.default.isExecutableFile(atPath: $0) } ?? "/usr/bin/python3"
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["-I", script, "--json"]
        let out = Pipe()
        p.standardOutput = out
        p.standardError = Pipe()
        do {
            try p.run()
            let data = out.fileHandleForReading.readDataToEndOfFile()
            p.waitUntilExit()
            return .success(try JSONDecoder().decode(Snapshot.self, from: data))
        } catch {
            return .failure(error)
        }
    }
}

// MARK: - App

@main
struct CableCheckApp: App {
    @StateObject private var model = Model()
    var body: some Scene {
        WindowGroup("Cable Check") {
            ContentView().environmentObject(model)
                .frame(minWidth: 940, minHeight: 760)
        }
        .windowResizability(.contentMinSize)
    }
}

struct ContentView: View {
    @EnvironmentObject var model: Model
    var showRaw: Bool { model.showRaw }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                header
                HStack(alignment: .top, spacing: 14) {
                    ForEach(model.ports) { PortCard(port: $0, ports: model.ports, showRaw: showRaw) }
                }
                if let err = model.error {
                    Label("Couldn't read the ports: \(err)", systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.red)
                }
                TestGuide()
                footer
            }
            .padding(24)
        }
        .background(Color(nsColor: .windowBackgroundColor))
    }

    var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Cable Check").font(.largeTitle.bold())
            Text("Plug in a USB-C cable. Results update on their own.")
                .foregroundStyle(.secondary)
        }
    }

    var footer: some View {
        HStack {
            Toggle("Show raw cable-chip data", isOn: $model.showRaw).toggleStyle(.checkbox)
            Spacer()
            Text("Updated \(model.updated.formatted(date: .omitted, time: .standard))")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}

// MARK: - Port card

struct PortCard: View {
    let port: PortReport
    let ports: [PortReport]
    let showRaw: Bool

    var accent: Color {
        switch port.mode {
        case "empty": return .secondary
        case "nodata": return .orange
        case "charger": return .yellow
        case "loop": return .purple
        default: return port.verdict.contains("BASIC") ? .orange : .green
        }
    }

    var modeLabel: String {
        switch port.mode {
        case "empty": return "EMPTY"
        case "loop": return "LOOP TEST"
        case "charger": return "CHARGER"
        case "nodata": return "NO DATA"
        default: return "DEVICE TEST"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("PORT \(port.number)").font(.caption.bold()).foregroundStyle(.secondary)
                Spacer()
                Text(modeLabel)
                    .font(.caption2.bold())
                    .padding(.horizontal, 7).padding(.vertical, 3)
                    .background(accent.opacity(0.18), in: Capsule())
                    .foregroundStyle(port.mode == "empty" ? Color.secondary : accent)
            }
            if port.mode == "empty" {
                Spacer(minLength: 0)
                VStack(spacing: 8) {
                    Image(systemName: "cable.connector").font(.system(size: 30))
                    Text("Plug a cable in here").font(.callout)
                }
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity)
                Spacer(minLength: 0)
            } else if port.loop_partner_reads_cable {
                Text(port.headline).font(.headline)
                Text("This is the other end of the cable. The results are shown on the other port.")
                    .font(.callout).foregroundStyle(.secondary)
            } else {
                Text(port.headline).font(.subheadline).foregroundStyle(.secondary)
                if !port.verdict.isEmpty {
                    // "DATA WORKS. 40Gbps ... cable, 240 W" -> status line + cable line
                    let parts = port.verdict.components(separatedBy: ". ")
                    if parts.count > 1, parts[0] == parts[0].uppercased() {
                        Text(parts[0]).font(.caption.bold()).foregroundStyle(accent)
                        Text(parts.dropFirst().joined(separator: ". ")).font(.title3.bold())
                            .fixedSize(horizontal: false, vertical: true)
                    } else {
                        Text(port.verdict).font(.title3.bold()).foregroundStyle(accent)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                ForEach(port.notes, id: \.self) {
                    Text($0).font(.callout).foregroundStyle(.secondary)
                }
                ForEach(Array(port.fields.enumerated()), id: \.offset) { _, f in
                    FieldRow(label: f.first ?? "", value: f.count > 1 ? f[1] : "")
                }
                if showRaw && !port.raw.isEmpty {
                    Text(port.raw).font(.caption.monospaced()).foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, minHeight: 300, alignment: .topLeading)
        .background(RoundedRectangle(cornerRadius: 14).fill(Color(nsColor: .controlBackgroundColor)))
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .strokeBorder(port.mode == "empty" ? Color.secondary.opacity(0.35) : accent.opacity(0.6),
                              style: StrokeStyle(lineWidth: port.mode == "empty" ? 1 : 2,
                                                 dash: port.mode == "empty" ? [5] : []))
        )
    }
}

struct FieldRow: View {
    let label: String
    let value: String
    var icon: String {
        switch label {
        case "Charging": return "bolt.fill"
        case "Data": return "arrow.left.arrow.right"
        case "Video": return "display"
        case "Type": return "cable.connector"
        case "Maker": return "tag"
        default: return "info.circle"
        }
    }
    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: icon).frame(width: 16).foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 1) {
                Text(label).font(.caption.bold()).foregroundStyle(.secondary)
                Text(value).font(.callout).fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
        }
    }
}

// MARK: - Which test tells you what

enum Answer { case yes, rated, partly, no }

struct TestRow: Identifiable {
    let id = UUID()
    let name: String
    let how: String
    let answers: [Answer]
    let note: String
}

struct TestGuide: View {
    let questions = ["Charging\nrating", "Charge-only\nor data?", "Fast data\n(5–10 Gbps)", "Thunderbolt\n(40 Gbps+)", "Actual link\nspeed"]

    let rows: [TestRow] = [
        TestRow(name: "Loop test", how: "Both ends into this Mac",
                answers: [.yes, .no, .rated, .rated, .no],
                note: "Reads the cable's chip only. Nothing is sent over the wires."),
        TestRow(name: "Phone", how: "Mac to phone",
                answers: [.yes, .yes, .partly, .no, .yes],
                note: "Most phones only do 480 Mbps. iPhone 15 Pro and newer Pros, and some Androids, reach 5–10 Gbps."),
        TestRow(name: "USB SSD / flash drive", how: "Mac to drive",
                answers: [.yes, .yes, .yes, .no, .yes],
                note: "Proves the fast wires work, up to the drive's own top speed."),
        TestRow(name: "Thunderbolt SSD or dock", how: "Mac to Thunderbolt device",
                answers: [.yes, .yes, .yes, .yes, .yes],
                note: "The only way here to prove a cable really does Thunderbolt speeds."),
        TestRow(name: "Charger / battery pack", how: "Mac to charger",
                answers: [.yes, .no, .rated, .rated, .no],
                note: "Power only, no data. Same as a loop test."),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Which test tells you what").font(.title2.bold())
            Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 10) {
                GridRow {
                    Text("Test").font(.caption.bold()).foregroundStyle(.secondary)
                    ForEach(questions, id: \.self) {
                        Text($0).font(.caption.bold()).foregroundStyle(.secondary)
                            .multilineTextAlignment(.center).gridColumnAlignment(.center)
                    }
                }
                Divider()
                ForEach(rows) { row in
                    GridRow {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(row.name).font(.callout.bold())
                            Text(row.how).font(.caption).foregroundStyle(.secondary)
                            Text(row.note).font(.caption).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                                .frame(maxWidth: 300, alignment: .leading)
                        }
                        ForEach(Array(row.answers.enumerated()), id: \.offset) { _, a in
                            AnswerBadge(answer: a)
                        }
                    }
                    Divider()
                }
            }
            HStack(spacing: 18) {
                legend(.yes, "Tested")
                legend(.rated, "Chip's claim only, not proven")
                legend(.partly, "Depends on the device")
                legend(.no, "Can't tell")
            }
            .font(.caption)
            Text("Charging power always comes from the cable's chip. Nothing here can measure real watts. A cable with no chip is a basic cable: up to 60 W, and USB 2.0 data at most.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(18)
        .background(RoundedRectangle(cornerRadius: 14).fill(Color(nsColor: .controlBackgroundColor)))
    }

    func legend(_ a: Answer, _ text: String) -> some View {
        HStack(spacing: 5) { AnswerBadge(answer: a, small: true); Text(text).foregroundStyle(.secondary) }
    }
}

struct AnswerBadge: View {
    let answer: Answer
    var small = false
    var body: some View {
        let (icon, color): (String, Color) = {
            switch answer {
            case .yes: return ("checkmark.circle.fill", .green)
            case .rated: return ("tag.circle.fill", .blue)
            case .partly: return ("circle.lefthalf.filled", .orange)
            case .no: return ("xmark.circle", .secondary)
            }
        }()
        Image(systemName: icon).foregroundStyle(color).font(small ? .caption : .title3)
    }
}
