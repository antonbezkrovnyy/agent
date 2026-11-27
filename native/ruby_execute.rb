# frozen_string_literal: true

require 'json'
require '/gems/mpsiem/lib/mpsiem'

rules_path, fixtures_path = ARGV
abort 'usage: ruby_execute.rb RULES.yml FIXTURES.json' unless rules_path && fixtures_path

fixtures = JSON.parse(File.read(fixtures_path))
normalizer = Mpsiem::Normalizer.new(
  rules_path,
  Mpsiem::Normalizer::Options.new('message', 'mime', 'normalizer_failure', 'unparsed')
)
positive = fixtures.fetch('positive', [])
outputs = normalizer.execute(positive.map { |fixture| fixture.fetch('event') })
normalizer.free
results = positive.each_with_index.map do |fixture, index|
  { 'name' => fixture['name'], 'actual' => outputs[index] }
end
puts JSON.pretty_generate('ok' => true, 'results' => results)
